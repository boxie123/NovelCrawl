"""业务编排层：连接浏览器抓取、SQLite 持久化和 TXT 导出。

一次普通下载的数据流如下：

    目录 URL
      → 匹配站点配置并启动浏览器
      → 抓取、解析目录
      → 把目录同步到 SQLite
      → 查询 pending/failed 章节
      → 分批并发抓取正文
      → 主协程逐章提交 SQLite
      → 整次任务结束后导出一次 TXT

本模块只负责“安排这些步骤以什么顺序发生”。具体页面解析在
``crawler.py`` / ``parsers.py``，具体 SQL 在 ``storage.py``，TXT 格式在
``exporter.py``。这种分层使每个部分能够单独测试。
"""

import asyncio
import logging
import re
from contextlib import asynccontextmanager
from typing import Dict, Iterable, List, Optional
from urllib.parse import urlparse

from playwright.async_api import BrowserContext, async_playwright
from playwright_stealth import Stealth

from novelcrawl.config import AppConfig
from novelcrawl.crawler import NovelCrawler
from novelcrawl.errors import InvalidChapterSpec, SiteConfigNotFound
from novelcrawl.exporter import TxtExporter
from novelcrawl.models import ChapterRecord, FetchResult, SiteConfig
from novelcrawl.site_config import get_site_config, match_site_config
from novelcrawl.storage import Storage
from novelcrawl.urls import normalize_url


logger = logging.getLogger(__name__)


BLOCKED_RESOURCE_TYPES = {"image", "font", "media", "stylesheet"}
HEADLESS_CHROME_PATTERN = re.compile(r"\bHeadlessChrome/(?P<version>[0-9.]+)")


def normalize_browser_user_agent(user_agent: str) -> str:
    """保留实际 Chromium 版本，仅移除默认 UA 中的 ``Headless`` 标记。

    Playwright 随浏览器升级而变化，不能再像旧实现一样硬编码 Chrome 114。
    正则只替换产品名称，平台、WebKit、版本号和 Safari 标识均保持原样；如果
    将来 Playwright 默认 UA 已不含该标记，则直接返回原值。
    """
    return HEADLESS_CHROME_PATTERN.sub(
        lambda match: "Chrome/{}".format(match.group("version")),
        user_agent,
        count=1,
    )


async def block_unneeded_resources(route) -> None:
    """拦截正文抓取不需要的静态资源。

    Args:
        route: Playwright 为每个网络请求提供的路由对象。

    图片、字体、音视频和样式表不影响 HTML 正文解析，终止这些请求可以减少
    流量和等待时间。其他资源仍需继续请求，否则页面脚本或 HTML 可能无法加载。
    """
    if route.request.resource_type in BLOCKED_RESOURCE_TYPES:
        await route.abort()
    else:
        await route.continue_()


class DownloadService:
    """执行下载、续传和刷新。

    Playwright 允许同一批章节并发抓取，但抓取任务本身不写数据库。所有
    ``Storage`` 调用均在收集结果的主协程中串行执行，从而避免多个异步任务
    同时争用 SQLite 写锁。

    Attributes:
        storage: SQLite 数据访问对象。
        exporter: TXT 导出对象。
        config: 批次大小、等待时间和重试次数等运行配置。
    """

    def __init__(
        self,
        storage: Storage,
        exporter: TxtExporter,
        config: AppConfig,
    ):
        """保存服务依赖，但此时不会启动浏览器或连接网站。"""
        self.storage = storage
        self.exporter = exporter
        self.config = config
        self.stealth = Stealth(init_scripts_only=True)

    @asynccontextmanager
    async def _crawler_session(
        self,
        site_config: SiteConfig,
        *,
        catalogue_url: str,
    ):
        """创建并自动清理一次抓取会话。

        Args:
            site_config: 当前网站的 CSS 选择器配置。
            catalogue_url: 当前来源目录 URL，作为章节第一页的 Referer。

        Yields:
            已绑定浏览器上下文和站点配置的 ``NovelCrawler``。

        ``@asynccontextmanager`` 让调用方可以使用 ``async with``。无论抓取
        成功、失败还是被用户取消，两个 ``finally`` 都会依次关闭 Context、
        Browser 和 Playwright 驱动，避免遗留 Chromium 进程。
        """
        playwright = await async_playwright().start()
        browser = None
        try:
            # 一个 Browser 可包含多个 Context；本项目每次任务使用独立 Context，
            # 使 Cookie、路由规则和超时设置不会泄漏到下一次任务。
            browser = await playwright.chromium.launch()

            # 先在不访问网络的临时页面中读取本版本 Chromium 的默认 UA，再只
            # 移除 Headless 产品标记。这样版本会随 Playwright 自动更新，不会
            # 再出现硬编码 Chrome 114 与实际浏览器版本不一致的问题。
            probe_context = await browser.new_context()
            try:
                probe_page = await probe_context.new_page()
                default_user_agent = await probe_page.evaluate(
                    "navigator.userAgent"
                )
            finally:
                await probe_context.close()

            context: BrowserContext = await browser.new_context(
                java_script_enabled=True,
                user_agent=normalize_browser_user_agent(default_user_agent),
            )
            await self.stealth.apply_stealth_async(context)
            # "**/*" 表示拦截该上下文中的全部请求，再由回调决定终止或继续。
            await context.route("**/*", block_unneeded_resources)
            context.set_default_timeout(self.config.navigation_timeout_ms)
            try:
                yield NovelCrawler(
                    context,
                    site_config,
                    catalogue_url=catalogue_url,
                    navigation_retries=self.config.navigation_retries,
                )
            finally:
                await context.close()
        finally:
            if browser is not None:
                await browser.close()
            await playwright.stop()

    @staticmethod
    def _site_name(url: str) -> str:
        """从域名提取用于文件名显示的简短站点名。

        例如 ``www.sudugu.org`` 返回 ``sudugu``。解析失败时返回
        ``unknown``，保证后续仍能生成输出文件名。
        """
        hostname = urlparse(url).hostname or "unknown"
        parts = hostname.split(".")
        return parts[-2] if len(parts) >= 2 else hostname

    async def download_url(
        self,
        catalogue_url: str,
        *,
        start_position: Optional[int] = None,
    ) -> int:
        """同步目录并下载未完成章节，同一个 URL 再次运行即为续传。

        Args:
            catalogue_url: 小说目录页 URL。
            start_position: 可选的目录起始位置（从 1 开始）。指定后，本次
                统计、下载队列和 TXT 导出都忽略更早的章节。

        Returns:
            数据库中的来源 ID，后续可用于 ``resume``、``refresh`` 和
            ``export`` 命令。

        Raises:
            SiteConfigNotFound: URL 未匹配任何受支持站点。

        重要：``start_position`` 是“本次任务范围”，不是删除或修改数据库中
        更早章节。那些章节仍保留在 SQLite 中。
        """
        # 规范化后再匹配和入库，避免同一 URL 因大小写或 fragment 不同而
        # 被误认为两个来源。
        normalized_catalogue_url = normalize_url(catalogue_url)
        site_config = match_site_config(normalized_catalogue_url)
        if site_config is None:
            raise SiteConfigNotFound(
                "没有匹配到站点配置: {}".format(normalized_catalogue_url)
            )

        async with self._crawler_session(
            site_config,
            catalogue_url=normalized_catalogue_url,
        ) as crawler:
            # 每次续传都重新同步目录，这样网站新增章节会先进入 pending 状态。
            title, catalogue = await crawler.fetch_catalogue(
                normalized_catalogue_url
            )
            site_name = self._site_name(normalized_catalogue_url)
            source_id = self.storage.upsert_source(
                catalogue_url=normalized_catalogue_url,
                title=title,
                site_name=site_name,
                site_config_key=site_config.key,
                output_name="{}_{}".format(title, site_name),
            )
            self.storage.sync_catalogue(source_id, catalogue)

            # start-position 不只是过滤请求，也定义本次任务的统计范围。
            progress = self.storage.get_progress(
                source_id,
                start_position=start_position,
            )
            print(
                "来源 {id}: {title}，目录 {total} 章，已完成 {completed}，"
                "待下载 {pending}，失败 {failed}".format(
                    id=source_id,
                    title=title,
                    total=progress.total,
                    completed=progress.completed,
                    pending=progress.pending,
                    failed=progress.failed,
                )
            )

            queue = self.storage.get_download_queue(
                source_id,
                start_position=start_position,
            )
            await self._run_chapters(
                crawler,
                queue,
                source_id=source_id,
                preserve_completed=False,
                progress_start_position=start_position,
            )

            # 整次下载结束后只导出一次，避免每批重写越来越大的 TXT。
            output_path = self.exporter.export_source(
                source_id,
                start_position=start_position,
            )
            if queue:
                print("本次下载结束，已导出: {}".format(output_path))
            else:
                print("没有待下载章节，已重新导出: {}".format(output_path))
            return source_id

    async def resume_source(
        self,
        source_id: int,
        *,
        start_position: Optional[int] = None,
    ) -> int:
        """根据数据库中的目录 URL 重新同步并继续指定来源。

        Args:
            source_id: 已保存来源的数据库主键。
            start_position: 可选的本次任务起始目录位置。

        Returns:
            与输入相同的来源 ID。

        ``resume`` 最终复用 ``download_url``，因此“首次下载”和“续传”不会
        维护两套容易产生差异的实现。
        """
        source = self.storage.get_source(source_id)
        resumed_source_id = await self.download_url(
            source.catalogue_url,
            start_position=start_position,
        )
        if resumed_source_id != source_id:
            raise RuntimeError("续传时来源身份发生变化")
        return source_id

    async def refresh_source(
        self,
        source_id: int,
        *,
        positions: Optional[Iterable[int]] = None,
    ) -> None:
        """重新抓取指定章节；失败时保留已有完整正文。

        Args:
            source_id: 要刷新的来源 ID。
            positions: 要刷新的目录位置集合；``None`` 表示全部章节。

        Raises:
            SiteConfigNotFound: 原来源引用的站点配置已不存在。
            InvalidChapterSpec: 用户指定了目录中不存在的位置。

        普通续传跳过 completed，而刷新会主动抓取 completed。重要：刷新失败
        时传入 ``preserve_completed=True``，存储层只记录错误，不清空旧正文。
        """
        source = self.storage.get_source(source_id)
        site_config = get_site_config(source.site_config_key)
        if site_config is None:
            raise SiteConfigNotFound(
                "来源 {} 的站点配置 {} 不存在".format(
                    source_id,
                    source.site_config_key,
                )
            )

        # 先转成 set：一方面去重，另一方面便于计算“用户请求但数据库未找到”
        # 的位置，避免悄悄忽略输入错误。
        requested_positions = (
            None if positions is None else set(int(value) for value in positions)
        )
        chapters = self.storage.get_chapters_by_positions(
            source_id,
            requested_positions,
        )
        if requested_positions is not None:
            found_positions = {chapter.position for chapter in chapters}
            missing = sorted(requested_positions - found_positions)
            if missing:
                raise InvalidChapterSpec(
                    "来源 {} 不存在目录位置: {}".format(
                        source_id,
                        ", ".join(str(value) for value in missing),
                    )
                )

        async with self._crawler_session(
            site_config,
            catalogue_url=source.catalogue_url,
        ) as crawler:
            # refresh 不重新解析或同步目录，但先访问目录页，使章节请求与正常
            # 浏览顺序一致，并在同一个 BrowserContext 中建立站点 Cookie。
            await crawler.warm_up(source.catalogue_url)
            await self._run_chapters(
                crawler,
                chapters,
                source_id=source_id,
                preserve_completed=True,
                progress_start_position=None,
            )
            output_path = self.exporter.export_source(source_id)
            if chapters:
                print("刷新结束，已导出: {}".format(output_path))
            else:
                print("没有可刷新的章节，已重新导出: {}".format(output_path))

    async def _run_chapters(
        self,
        crawler: NovelCrawler,
        chapters: List[ChapterRecord],
        *,
        source_id: int,
        preserve_completed: bool,
        progress_start_position: Optional[int],
    ) -> None:
        """按配置分批抓取，并在批次之间限速。

        Args:
            crawler: 已打开浏览器会话的抓取器。
            chapters: 本次需要处理的章节。
            source_id: 用于查询进度的来源 ID。
            preserve_completed: 失败时是否保留已有正文；刷新为 ``True``。
            progress_start_position: 进度统计的起点；普通全量任务为 ``None``。

        重要：本函数只下载和报告进度，不导出 TXT。调用者必须在所有批次
        完成后导出一次，避免大文件在每个批次后被反复重写。
        """
        batch_size = self.config.batch_size
        # “向上取整”的整数写法：例如 7 章、每批 3 章，共 3 批。
        total_batches = (
            (len(chapters) + batch_size - 1) // batch_size
            if chapters
            else 0
        )
        for batch_index in range(total_batches):
            # 切片的结束位置可以超过列表长度，Python 会安全返回剩余元素。
            batch = chapters[
                batch_index * batch_size:(batch_index + 1) * batch_size
            ]
            await self._run_batch(
                crawler,
                batch,
                preserve_completed=preserve_completed,
            )
            progress = self.storage.get_progress(
                source_id,
                start_position=progress_start_position,
            )
            print(
                "批次 {current}/{total} 完成；总进度 {completed}/{chapter_total}，"
                "失败 {failed}".format(
                    current=batch_index + 1,
                    total=total_batches,
                    completed=progress.completed,
                    chapter_total=progress.total,
                    failed=progress.failed,
                )
            )
            if batch_index + 1 < total_batches:
                # 最后一批完成后无需等待，因为已经没有后续网站请求。
                await asyncio.sleep(self.config.batch_wait_seconds)

    async def _run_batch(
        self,
        crawler: NovelCrawler,
        chapters: List[ChapterRecord],
        *,
        preserve_completed: bool,
    ) -> None:
        """并发抓取一批章节，并对未成功章节进行有限次数重试。

        Args:
            crawler: 当前站点抓取器。
            chapters: 同一批需要并发处理的章节。
            preserve_completed: 记录最终失败时是否保留数据库中的旧正文。

        ``unresolved`` 保存仍需重试的章节；``attempts`` 记录本次运行实际
        尝试次数；``last_failures`` 保存最后一次错误，用于重试耗尽后入库。
        成功章节会立即从 unresolved 移除，因此下一轮不会重复请求。
        """
        unresolved: Dict[int, ChapterRecord] = {
            chapter.id: chapter for chapter in chapters
        }
        attempts: Dict[int, int] = {
            chapter.id: 0 for chapter in chapters
        }
        last_failures: Dict[int, FetchResult] = {}

        # 每轮只重试仍未成功的章节，已成功结果会立即事务提交。
        for _ in range(self.config.max_attempts_per_run):
            if not unresolved:
                break
            current_chapters = list(unresolved.values())
            results = await asyncio.gather(
                *[
                    self._safe_fetch(crawler, chapter)
                    for chapter in current_chapters
                ]
            )
            # asyncio.gather 保持输入任务的结果顺序，但这里按 chapter_id
            # 识别章节，因此即使将来顺序变化也不会写错记录。
            for result in results:
                attempts[result.chapter_id] += 1
                if result.succeeded:
                    # 重要：正文和 completed 状态由存储层在同一事务内提交。
                    self.storage.save_completed_chapter(
                        result.chapter_id,
                        title=result.title,
                        content=result.content or "",
                        attempts=attempts[result.chapter_id],
                    )
                    unresolved.pop(result.chapter_id, None)
                    last_failures.pop(result.chapter_id, None)
                else:
                    last_failures[result.chapter_id] = result

        # 只有所有重试均未成功的章节才会走到这里。
        for chapter_id, result in last_failures.items():
            self.storage.save_failed_chapter(
                chapter_id,
                error_type=result.error_type or "unknown",
                error_message=result.error_message or "未知错误",
                attempts=attempts[chapter_id],
                preserve_completed=preserve_completed,
            )

    @staticmethod
    async def _safe_fetch(
        crawler: NovelCrawler,
        chapter: ChapterRecord,
    ) -> FetchResult:
        """把意外异常转换为单章失败，避免取消同批其他任务。

        Args:
            crawler: 执行实际网络抓取的对象。
            chapter: 当前章节记录。

        Returns:
            无论成功还是失败都返回 ``FetchResult``，不向批次抛出异常。

        ``NovelCrawler.fetch_chapter`` 已处理常见错误，这里仍保留最后一道
        防线。否则 ``asyncio.gather`` 中一个意外异常会使整批提前失败。
        """
        try:
            return await crawler.fetch_chapter(chapter)
        except Exception as exc:
            logger.exception(
                "章节抓取任务向批次泄漏了未预期异常：chapter_id=%s url=%s",
                chapter.id,
                chapter.url,
            )
            return FetchResult(
                chapter_id=chapter.id,
                title=(
                    chapter.normalized_title
                    or chapter.catalogue_title
                    or "目录第 {} 章".format(chapter.position)
                ),
                content=None,
                error_type="unknown",
                error_message=(
                    "{}；详细堆栈见 logs/novelcrawl.log".format(exc)
                ),
            )
