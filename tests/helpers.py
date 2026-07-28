"""多个测试文件共享的准备和清理工具。

``unittest`` 的基本单位是继承 ``unittest.TestCase`` 的类。框架会针对每个
``test_*`` 方法创建一个新的测试类实例，然后按以下顺序调用：

    setUp() → 当前 test_* 方法 → tearDown()

因此每个测试都会获得全新的临时目录和数据库，互不污染。
"""

import tempfile
import unittest
from pathlib import Path

from novelcrawl.models import CatalogueChapter
from novelcrawl.storage import Storage


class StorageTestCase(unittest.TestCase):
    """为需要 SQLite 的测试提供隔离环境和常用造数方法。"""

    def setUp(self):
        """在每个测试开始前创建临时目录和空数据库。

        ``TemporaryDirectory`` 由操作系统分配随机路径。测试不使用项目真实的
        ``data/novelcrawl.db``，所以无论测试成功还是失败都不会损坏用户数据。
        """
        self.temporary_directory = tempfile.TemporaryDirectory()
        # pathlib.Path 比手工拼接字符串更清晰，并能跨操作系统工作。
        self.root = Path(self.temporary_directory.name)
        self.storage = Storage(self.root / "novelcrawl.db")
        self.storage.open()
        self.storage.initialize()

    def tearDown(self):
        """在每个测试结束后关闭数据库并删除临时目录。

        即使测试中的断言（assertion）失败，unittest 仍会调用 tearDown。
        Windows 不允许删除仍被 SQLite 占用的文件，所以必须先 close。
        """
        self.storage.close()
        self.temporary_directory.cleanup()

    def create_source(self, title="测试小说", suffix="example"):
        """快速创建一个测试来源，并返回数据库生成的来源 ID。

        参数提供默认值，大多数测试直接调用 ``create_source()``；需要两个
        独立来源的合并测试则传不同 suffix，保证 catalogue_url 唯一。
        """
        return self.storage.upsert_source(
            catalogue_url="https://{}.com/book/".format(suffix),
            title=title,
            site_name=suffix,
            site_config_key="sudugu",
            output_name="{}_{}".format(title, suffix),
        )

    def sync_three_chapters(self, source_id):
        """为指定来源写入三章固定目录，返回构造的目录对象。

        列表推导式会让 position 依次取 1、2、3。这里仅同步目录，三章初始
        状态都是 pending，没有正文。
        """
        chapters = [
            CatalogueChapter(
                url="https://example.com/chapter/{}".format(position),
                title="第{}章 原标题".format(position),
                normalized_title="第{}章 原标题".format(position),
                position=position,
            )
            for position in range(1, 4)
        ]
        self.storage.sync_catalogue(source_id, chapters)
        return chapters
