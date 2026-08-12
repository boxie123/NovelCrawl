"""抓取层的页面重试测试。

这里不启动真实浏览器，也不访问小说网站。测试用的 ``FakePage`` 只实现
``NovelCrawler._page_goto`` 会调用的少量 Playwright 接口，因此我们能稳定
验证“遇到网络错误页时是否真的点击刷新链接”。
"""

import unittest

from novelcrawl.crawler import NovelCrawler
from novelcrawl.models import ChapterRecord, SiteConfig


ERROR_HTML = """
<html>
  <body>
    <h1>出错了</h1>
    <p>网络错误,请点击刷新按钮重试</p>
    <a href="/catalog/1">刷新</a>
  </body>
</html>
"""
CATALOGUE_HTML = """
<html>
  <body>
    <h1>测试小说</h1>
    <ul><li><a href="/chapter/1">第一章</a></li></ul>
  </body>
</html>
"""


class FakeResponse:
    """模拟 Playwright 的 Response，只提供本测试关心的状态码。"""

    status = 200


class FakeRefreshLink:
    """模拟刷新链接定位器，并把点击动作交给所属页面记录。"""

    def __init__(self, page):
        self.page = page

    @property
    def first(self):
        """Playwright 的 ``locator.first`` 是属性，因此模拟对象也保持一致。"""
        return self

    async def count(self):
        """返回 1 表示页面上确实存在一个可点击的刷新链接。"""
        return 1

    async def click(self):
        """记录点击，并模拟网站在点击后返回正常目录。"""
        self.page.refresh_clicks += 1
        self.page.html = CATALOGUE_HTML


class FakePage:
    """模拟先返回 HTTP 200 错误页、点击刷新后恢复的浏览器页面。"""

    def __init__(self):
        self.html = ERROR_HTML
        self.refresh_clicks = 0
        self.goto_calls = 0

    async def goto(self, url, wait_until, referer=None):
        """首次导航成功，但页面正文是站点的临时错误提示。"""
        self.goto_calls += 1
        self.last_referer = referer
        return FakeResponse()

    async def content(self):
        """返回页面当前 HTML；点击刷新前后，该值会发生变化。"""
        return self.html

    def locator(self, selector, has_text):
        """验证代码查找的是包含“刷新”文字的链接。"""
        if selector != "a" or has_text != "刷新":
            raise AssertionError("刷新链接定位条件不正确")
        return FakeRefreshLink(self)

    async def wait_for_load_state(self, state):
        """真实浏览器会等待新页面 DOM 完成；模拟页面无需实际等待。"""
        if state != "domcontentloaded":
            raise AssertionError("等待的页面状态不正确")


class SelfLinkedChapterPage:
    """模拟“下一页”链接错误地指向当前分页的章节页面。"""

    def __init__(self):
        self.goto_calls = 0

    async def goto(self, url, wait_until, referer=None):
        """记录导航次数；若重复导航，测试末尾的断言会失败。"""
        self.goto_calls += 1
        self.last_referer = referer
        return FakeResponse()

    async def content(self):
        """返回带有正文及当前页链接的 HTML。"""
        return """
        <div class="content">本页正文</div>
        <div class="page">
          <a href="/chapter/1">上一页</a>
          <a href="/chapter/1#content">下一页（快捷键→）</a>
        </div>
        """

    async def close(self):
        """保持与真实 Playwright Page 的关闭接口一致。"""


class PaginatedChapterPage:
    """模拟两页正文，并记录每次导航携带的 Referer。"""

    def __init__(self):
        self.current_url = None
        self.navigation_records = []

    async def goto(self, url, wait_until, referer=None):
        self.current_url = url
        self.navigation_records.append((url, referer))
        return FakeResponse()

    async def content(self):
        if self.current_url.endswith("/1"):
            return """
            <div class="content">第一页正文</div>
            <div class="page"><a href="/chapter/2">下一页（快捷键→）</a></div>
            """
        return """
        <div class="content">第二页正文</div>
        <div class="page"><a href="/catalog">目录</a></div>
        """

    async def close(self):
        """模拟关闭标签页。"""


class FakeContext:
    """每次 ``new_page`` 都返回测试预先提供的页面对象。"""

    def __init__(self, page):
        self.page = page

    async def new_page(self):
        return self.page


class CrawlerRetryTests(unittest.IsolatedAsyncioTestCase):
    """验证需要 ``await`` 的异步抓取流程。

    ``IsolatedAsyncioTestCase`` 会为每个测试创建独立事件循环，因此测试方法
    可以直接写成 ``async def``，不需要读者自己调用 ``asyncio.run``。
    """

    def setUp(self):
        """创建最小站点配置；本测试不实际使用其中的 CSS 选择器。"""
        config = SiteConfig(
            key="test",
            domains=("example.com",),
            catalogue_selector="ul li",
            catalogue_button_selector="",
            catalogue_title_selector="h1",
            novel_selector="div.content",
            novel_button_selector="",
        )
        # context 在直接测试私有导航方法时不会被访问，传 None 即可。
        self.crawler = NovelCrawler(None, config, navigation_retries=3)

    async def test_clicks_refresh_link_when_http_200_page_reports_network_error(self):
        """HTTP 200 错误页应点击刷新链接，并返回点击后得到的正常 HTML。"""
        page = FakePage()

        result = await self.crawler._page_goto(
            page,
            "https://example.com/catalog/1",
        )

        # assertEqual 的第一个参数是实际结果，第二个参数是预期值。
        self.assertEqual(result, CATALOGUE_HTML)
        self.assertEqual(page.refresh_clicks, 1)
        # 关键断言：恢复动作是点击链接，而不是再次调用 goto。
        self.assertEqual(page.goto_calls, 1)

    async def test_current_page_next_link_does_not_navigate_twice(self):
        """下一页规范化后仍是当前 URL 时，应结束翻页而非再次请求。"""
        page = SelfLinkedChapterPage()
        config = SiteConfig(
            key="test",
            domains=("example.com",),
            catalogue_selector="ul li",
            catalogue_button_selector="",
            catalogue_title_selector="h1",
            novel_selector="div.content",
            novel_button_selector="div.page a",
        )
        crawler = NovelCrawler(FakeContext(page), config)
        chapter = ChapterRecord(
            id=1,
            source_id=1,
            url="https://example.com/chapter/1",
            position=1,
            catalogue_title="第一章",
            normalized_title="第1章",
            status="pending",
            content=None,
            last_error_type=None,
            last_error=None,
        )

        result = await crawler.fetch_chapter(chapter)

        self.assertTrue(result.succeeded)
        self.assertEqual(result.content, "本页正文")
        self.assertEqual(page.goto_calls, 1)

    async def test_chapter_pages_use_catalogue_then_previous_page_as_referer(self):
        """第一页引用目录，后续分页引用上一页，且正文完整拼接。"""
        page = PaginatedChapterPage()
        config = SiteConfig(
            key="test",
            domains=("example.com",),
            catalogue_selector="ul li",
            catalogue_button_selector="",
            catalogue_title_selector="h1",
            novel_selector="div.content",
            novel_button_selector="div.page a",
        )
        crawler = NovelCrawler(
            FakeContext(page),
            config,
            catalogue_url="https://example.com/catalog",
        )
        chapter = ChapterRecord(
            id=1,
            source_id=1,
            url="https://example.com/chapter/1",
            position=1,
            catalogue_title="第一章",
            normalized_title="第1章",
            status="pending",
            content=None,
            last_error_type=None,
            last_error=None,
        )

        result = await crawler.fetch_chapter(chapter)

        self.assertTrue(result.succeeded)
        self.assertEqual(result.content, "第一页正文第二页正文")
        self.assertEqual(
            page.navigation_records,
            [
                (
                    "https://example.com/chapter/1",
                    "https://example.com/catalog",
                ),
                (
                    "https://example.com/chapter/2",
                    "https://example.com/chapter/1",
                ),
            ],
        )
