"""纯 HTML 与标题解析测试。

解析函数没有网络或数据库副作用，所以测试只需要构造最小 HTML 字符串。
这种测试比访问真实网站稳定：网站临时不可用不会让测试随机失败。
"""

import unittest

from novelcrawl.models import SiteConfig
from novelcrawl.parsers import (
    normalize_chapter_title,
    parse_catalogue,
    parse_chapter_text,
)


class ParserTests(unittest.TestCase):
    """验证目录去重、正文清理和中文章节号转换。"""

    def setUp(self):
        """每个测试前创建一份只匹配测试 HTML 的站点配置。"""
        self.config = SiteConfig(
            key="test",
            domains=("example.com",),
            catalogue_selector="ul.chapters li",
            catalogue_button_selector="",
            catalogue_title_selector="h1",
            novel_selector="div.content",
            novel_button_selector="",
        )

    def test_catalogue_deduplicates_repeated_urls(self):
        """相同章节 URL 重复出现时只保留第一次。"""
        # 准备：第一章出现两次且标题不同，第二章只出现一次。
        html = """
        <h1>测试小说</h1>
        <ul class="chapters">
          <li><a href="/1">第一章 开始</a></li>
          <li><a href="/1">第一章 重复</a></li>
          <li><a href="/2">第二章 后续</a></li>
        </ul>
        """
        # 执行：传入单页列表；相对链接 /1、/2 会基于 catalogue_url 补全。
        title, chapters = parse_catalogue(
            [html],
            catalogue_url="https://example.com/book/",
            config=self.config,
        )
        # 验证标题、去重数量、连续位置和中文数字规范化。
        self.assertEqual(title, "测试小说")
        self.assertEqual(len(chapters), 2)
        self.assertEqual([chapter.position for chapter in chapters], [1, 2])
        self.assertEqual(chapters[0].normalized_title, "第1章 开始")

    def test_chapter_parser_removes_nested_divs(self):
        """正文容器内的嵌套 div 应作为广告块移除。"""
        html = """
        <div class="content">
          正文
          <div>广告</div>
          后续
        </div>
        """
        content = parse_chapter_text(html, "div.content")
        # assertIn 检查子串存在，assertNotIn 检查子串不存在。
        self.assertIn("正文", content)
        self.assertIn("后续", content)
        self.assertNotIn("广告", content)

    def test_chinese_chapter_number_is_normalized(self):
        """标准中文章节号应转换成阿拉伯数字。"""
        self.assertEqual(
            normalize_chapter_title("第一百二十三章 标题"),
            "第123章 标题",
        )
