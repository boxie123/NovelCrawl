"""TXT 导出格式和原子写入行为测试。

测试会在临时目录真正生成小型 TXT，然后读取内容进行断言。它不访问真实
``exports/``，tearDown 会在测试后删除临时文件。
"""

from novelcrawl.exporter import TxtExporter

from tests.helpers import StorageTestCase


class ExporterTests(StorageTestCase):
    """验证章节顺序、错误占位、临时文件清理和起始位置过滤。"""

    def test_exports_all_positions_with_failure_and_pending_placeholders(self):
        """三种状态都应按目录顺序出现在 TXT 中。"""
        # 准备：第一章 completed，第二章 failed，第三章保持 pending。
        source_id = self.create_source()
        self.sync_three_chapters(source_id)
        chapters = self.storage.get_chapters_by_positions(source_id)

        self.storage.save_completed_chapter(
            chapters[0].id,
            title="第1章 成功",
            content="第一章正文",
        )
        self.storage.save_failed_chapter(
            chapters[1].id,
            error_type="selector_missing",
            error_message="选择器未匹配",
        )

        # 导出目录位于 setUp 创建的临时根目录。
        exporter = TxtExporter(
            self.storage,
            self.root / "exports" / "sources",
            self.root / "exports" / "merged",
        )
        # 执行导出并以同样的 UTF-8 编码读回。
        output_path = exporter.export_source(source_id)
        text = output_path.read_text(encoding="utf-8")

        # text.index 返回子串首次出现位置；assertLess 验证前者位置更小，
        # 从而证明章节顺序为 1 → 2 → 3。
        self.assertLess(text.index("第1章 成功"), text.index("第2章 原标题"))
        self.assertLess(text.index("第2章 原标题"), text.index("第3章 原标题"))
        self.assertIn("[本章下载失败]", text)
        self.assertIn("选择器未匹配", text)
        self.assertIn("[本章尚未下载]", text)
        # assertFalse 验证成功导出后没有遗留中间 .tmp 文件。
        self.assertFalse(output_path.with_suffix(".txt.tmp").exists())

    def test_start_position_excludes_earlier_pending_chapters(self):
        """从位置 2 导出时，位置 1 即使 pending 也不应出现在 TXT。"""
        # 准备：三章都保持 pending，便于确认过滤发生在查询范围而非状态上。
        source_id = self.create_source()
        self.sync_three_chapters(source_id)
        exporter = TxtExporter(
            self.storage,
            self.root / "exports" / "sources",
            self.root / "exports" / "merged",
        )

        # 执行：显式指定 start_position=2。
        output_path = exporter.export_source(
            source_id,
            start_position=2,
        )
        text = output_path.read_text(encoding="utf-8")

        # 第 1 章不存在，第 2、3 章存在，说明起点语义正确。
        self.assertNotIn("第1章 原标题", text)
        self.assertIn("第2章 原标题", text)
        self.assertIn("第3章 原标题", text)
