"""Playwright 抓取层，只负责取得目录和章节内容，不写数据库。

生命周期和数据流：

    DownloadService 创建 BrowserContext
      → NovelCrawler 为目录或章节创建 Page
      → Page 导航并取得 HTML
      → parsers.py 把 HTML 转成结构化数据/正文
      → NovelCrawler 关闭 Page 并返回结果

Page 类似浏览器标签页，BrowserContext 类似独立的浏览器用户会话。本类不
拥有 Context，因此只关闭自己创建的 Page；Context 由 ``DownloadService``
在整次任务结束时统一关闭。
"""

import asyncio
import logging
from typing import List, Optional, Set, Tuple
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from playwright.async_api import (
    BrowserContext,
    Error as PlaywrightError,
    TimeoutError as PlaywrightTimeoutError,
)

from novelcrawl.errors import PageNavigationError
from novelcrawl.models import (
    CatalogueChapter,
    ChapterRecord,
    FetchResult,
    SiteConfig,
)
from novelcrawl.parsers import (
    normalize_chapter_title,
    parse_catalogue,
    parse_chapter_text,
    validate_chapter_content,
)
from novelcrawl.urls import normalize_url


logger = logging.getLogger(__name__)


def _temporary_error_page_message(html: str) -> Optional[str]:
    """识别“请求成功，但返回了临时错误提示”的页面。

    有些网站发生临时故障时仍返回 HTTP 200。若只检查状态码，程序会把错误页
    交给目录/正文解析器，最终产生“CSS 选择器未匹配”这种误导性错误。

    当前只识别同时包含“网络错误”“重试”和刷新链接的页面，避免把小说正文中
    偶然出现的“刷新”二字误判为错误页。返回值用于最终错误提示；正常页面返回
    ``None``。
    """
    soup = BeautifulSoup(html, "lxml")
    page_text = soup.get_text(" ", strip=True)
    refresh_link = next(
        (
            anchor
            for anchor in soup.find_all("a")
            if "刷新" in anchor.get_text(strip=True)
        ),
        None,
    )
    if "网络错误" in page_text and "重试" in page_text and refresh_link is not None:
        return "网络错误，请点击刷新按钮重试"
    return None


class NovelCrawler:
    """使用一个浏览器上下文抓取某个站点配置对应的页面。

    Attributes:
        context: Playwright 浏览器上下文，用来创建页面。
        config: 当前站点的目录和正文 CSS 选择器。
        navigation_retries: 单个页面导航失败时的最大尝试次数。
    """

    def __init__(
        self,
        context: BrowserContext,
        config: SiteConfig,
        *,
        navigation_retries: int = 5,
    ):
        """保存浏览器上下文和站点规则，不会立即创建标签页。"""
        self.context = context
        self.config = config
        self.navigation_retries = navigation_retries

    async def _recover_temporary_error_page(
        self,
        page,
        url: str,
        html: str,
    ) -> str:
        """按网页提供的刷新链接重试，而不是重新执行 ``page.goto``。

        ``1qxs`` 的临时错误页中提供了一个“刷新”链接。人工点击后能够进入真实
        页面，直接重新加载 URL 却不一定等价，因此这里让 Playwright 实际点击
        链接。``navigation_retries`` 包含第一次打开页面：默认值 5 表示首次
        访问失败后，最多再点击刷新链接 4 次。
        """
        message = _temporary_error_page_message(html)
        if message is None:
            return html

        last_error = RuntimeError("网站返回临时错误页: {}".format(message))
        for refresh_attempt in range(2, self.navigation_retries + 1):
            logger.warning(
                "检测到网站临时错误页，正在点击刷新链接：url=%s attempt=%s/%s",
                url,
                refresh_attempt,
                self.navigation_retries,
            )
            try:
                # has_text 允许链接文字是“点击刷新”或“刷新按钮”，不依赖 href
                # 的具体形式；first 防止页面意外出现多个同名链接。
                refresh_link = page.locator("a", has_text="刷新").first
                if await refresh_link.count() == 0:
                    last_error = RuntimeError(
                        "网站返回临时错误页，但没有找到可点击的刷新链接"
                    )
                    break
                await refresh_link.click()
                await page.wait_for_load_state("domcontentloaded")
                html = await page.content()
                message = _temporary_error_page_message(html)
                if message is None:
                    return html
                last_error = RuntimeError("网站仍返回临时错误页: {}".format(message))
            except PlaywrightTimeoutError as exc:
                last_error = exc

            if refresh_attempt < self.navigation_retries:
                # 连续点击过快可能加重站点限流，沿用导航失败时的等待间隔。
                await asyncio.sleep(5.0)

        raise PageNavigationError(
            "{} 获取失败，点击页面刷新链接后仍未恢复: {}".format(
                url,
                last_error,
            )
        )

    async def _page_goto(self, page, url: str) -> str:
        """访问 URL，并在有限重试后返回完整页面 HTML。

        Args:
            page: Playwright Page，即当前浏览器标签页。
            url: 要访问的绝对 URL。

        Returns:
            HTTP 200 页面对应的 HTML 字符串。

        Raises:
            PlaywrightTimeoutError: 最后一次失败是页面超时。
            RuntimeError: 页面持续无响应或返回非 200 状态。

        ``domcontentloaded`` 只等待 HTML DOM 建立，不等待图片等全部资源，
        对正文抓取比 ``networkidle`` 更合适。
        """
        last_error = None
        for attempt in range(1, self.navigation_retries + 1):
            try:
                response = await page.goto(url, wait_until="domcontentloaded")
                if response is not None and response.status == 200:
                    html = await page.content()
                    return await self._recover_temporary_error_page(
                        page,
                        url,
                        html,
                    )
                status = response.status if response is not None else "无响应"
                last_error = RuntimeError("HTTP 状态: {}".format(status))
            except PlaywrightTimeoutError as exc:
                last_error = exc

            if attempt < self.navigation_retries:
                # 最后一次失败后直接抛错，不再进行无意义等待。
                await asyncio.sleep(5.0)

        if isinstance(last_error, PlaywrightTimeoutError):
            raise last_error
        raise PageNavigationError("{} 获取失败: {}".format(url, last_error))

    async def fetch_catalogue(
        self,
        catalogue_url: str,
    ) -> Tuple[str, List[CatalogueChapter]]:
        """抓取可能包含多页的目录，并解析小说名和章节列表。

        Args:
            catalogue_url: 小说目录页 URL。

        Returns:
            二元组：小说标题，以及按目录顺序排列的章节列表。

        目录分页复用同一个 Page，所有 HTML 收集完后再统一交给纯解析函数，
        因而解析逻辑可以在不启动浏览器的单元测试中验证。
        """
        html_pages = []
        page = await self.context.new_page()
        visited_urls: Set[str] = set()
        url = normalize_url(catalogue_url)

        try:
            while True:
                # 防止站点的“下一页”按钮错误地指回已经访问的目录页。
                if url in visited_urls:
                    break
                visited_urls.add(url)

                html = await self._page_goto(page, url)
                html_pages.append(html)

                # 空选择器表示该站点目录只有一页。
                selector = self.config.catalogue_button_selector
                if not selector:
                    break

                soup = BeautifulSoup(html, "lxml")
                buttons = soup.select(selector)
                if not buttons:
                    break

                # 现有站点配置约定：目录翻页区域最后一个按钮是“下一页”。
                next_href = buttons[-1].get("href")
                if not next_href or next_href == "javascript:void(0);":
                    break

                next_url = normalize_url(urljoin(url, next_href))
                if next_url in visited_urls:
                    break
                url = next_url
        finally:
            # 即使目录解析或网络访问抛错，也必须关闭当前标签页。
            await page.close()

        return parse_catalogue(
            html_pages,
            catalogue_url=catalogue_url,
            config=self.config,
        )

    async def fetch_chapter(self, chapter: ChapterRecord) -> FetchResult:
        """抓取单章全部分页，任何预期失败都转换为 FetchResult。

        Args:
            chapter: 数据库查询出的章节记录，包含 URL、标题和目录位置。

        Returns:
            ``FetchResult``。成功时包含完整正文；失败时包含稳定的错误类型和
            可读错误信息，供存储层记录及 TXT 占位显示。

        一章可能被网站拆成多个页面。本方法顺序访问这些页面并拼接正文，但
        不会把“半章”作为成功结果返回。
        """
        # 目录标题在抓取失败时仍可用于错误占位；若标题缺失，则用位置兜底。
        title = normalize_chapter_title(
            chapter.catalogue_title
            or chapter.normalized_title
            or "目录第 {} 章".format(chapter.position)
        )
        page = None
        url = chapter.url
        visited_urls: Set[str] = set()
        page_texts = []

        try:
            page = await self.context.new_page()
            while True:
                # visited_urls 防止错误翻页链接形成死循环。
                if url in visited_urls:
                    return FetchResult(
                        chapter_id=chapter.id,
                        title=title,
                        content=None,
                        error_type="pagination_loop",
                        error_message="检测到重复正文翻页 URL: {}".format(url),
                    )
                visited_urls.add(url)

                html = await self._page_goto(page, url)
                page_text = parse_chapter_text(
                    html,
                    self.config.novel_selector,
                )
                if page_text is None:
                    return FetchResult(
                        chapter_id=chapter.id,
                        title=title,
                        content=None,
                        error_type="selector_missing",
                        error_message="正文选择器未匹配: {}".format(
                            self.config.novel_selector
                        ),
                    )
                page_texts.append(page_text)

                # 空选择器表示正文不会分页。
                selector = self.config.novel_button_selector
                if not selector:
                    break

                soup = BeautifulSoup(html, "lxml")
                buttons = soup.select(selector)
                if not buttons:
                    break

                # 现有站点配置约定：正文导航区最后一个按钮可能是“下一页”。
                next_button = buttons[-1]
                next_href = next_button.get("href")
                if next_button.get_text(strip=True) != "下一页" or not next_href:
                    break

                next_url = normalize_url(urljoin(url, next_href))
                if next_url in visited_urls:
                    return FetchResult(
                        chapter_id=chapter.id,
                        title=title,
                        content=None,
                        error_type="pagination_loop",
                        error_message="检测到重复正文翻页 URL: {}".format(next_url),
                    )
                url = next_url

            # 只有所有分页都成功解析后才合并和验证正文。
            content = "".join(page_texts)
            error_type, error_message = validate_chapter_content(content)
            if error_type:
                return FetchResult(
                    chapter_id=chapter.id,
                    title=title,
                    content=None,
                    error_type=error_type,
                    error_message=error_message,
                )

            return FetchResult(
                chapter_id=chapter.id,
                title=title,
                content=content,
            )
        except PlaywrightTimeoutError as exc:
            return FetchResult(
                chapter_id=chapter.id,
                title=title,
                content=None,
                error_type="timeout",
                error_message=str(exc),
            )
        except (PlaywrightError, PageNavigationError) as exc:
            return FetchResult(
                chapter_id=chapter.id,
                title=title,
                content=None,
                error_type="network",
                error_message=str(exc),
            )
        except Exception as exc:
            # exception() 会自动附带当前 except 上下文的完整 traceback。
            logger.exception(
                "抓取章节时发生未预期错误：chapter_id=%s url=%s",
                chapter.id,
                chapter.url,
            )
            return FetchResult(
                chapter_id=chapter.id,
                title=title,
                content=None,
                error_type="unknown",
                error_message=(
                    "{}；详细堆栈见 logs/novelcrawl.log".format(exc)
                ),
            )
        finally:
            if page is not None:
                try:
                    await page.close()
                except Exception:
                    # 关闭标签页失败不应覆盖前面已经形成的抓取结果或错误。
                    pass
