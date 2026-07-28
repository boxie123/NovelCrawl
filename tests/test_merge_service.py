"""跨来源合并功能测试。

此测试验证合并版保存的是来源章节引用，而不是正文副本。若来源正文刷新，
无需重建合并规则，下一次导出就应自动使用新内容。
"""

from novelcrawl.exporter import TxtExporter
from novelcrawl.merge_service import MergeService

from tests.helpers import StorageTestCase


class MergeServiceTests(StorageTestCase):
    """验证范围追加、多 part 组合及动态引用。"""

    def setUp(self):
        """先执行父类数据库准备，再创建每个测试专用的合并服务。"""
        # super().setUp() 很重要：它会创建 self.root 和 self.storage。
        super().setUp()
        self.exporter = TxtExporter(
            self.storage,
            self.root / "exports" / "sources",
            self.root / "exports" / "merged",
        )
        self.merge_service = MergeService(self.storage, self.exporter)

    def test_range_and_composed_chapter_use_current_source_content(self):
        """来源正文更新后，合并版应自动反映最新正文。"""
        # 准备两个独立来源。不同 suffix 会生成不同 catalogue_url。
        source_a = self.create_source(title="来源A", suffix="source-a")
        source_b = self.create_source(title="来源B", suffix="source-b")
        self.sync_three_chapters(source_a)
        self.sync_three_chapters(source_b)

        # 取得要引用的三个数据库章节，并分别保存正文。
        chapter_a1 = self.storage.get_chapters_by_positions(source_a, [1])[0]
        chapter_a2 = self.storage.get_chapters_by_positions(source_a, [2])[0]
        chapter_b1 = self.storage.get_chapters_by_positions(source_b, [1])[0]
        self.storage.save_completed_chapter(
            chapter_a1.id,
            title="A1",
            content="A1正文",
        )
        self.storage.save_completed_chapter(
            chapter_a2.id,
            title="A2",
            content="A2正文",
        )
        self.storage.save_completed_chapter(
            chapter_b1.id,
            title="B1",
            content="B1旧正文",
        )

        # 创建合并版：先一对一追加 A1，再把 A2 和 B1 组合为第二章。
        edition_id = self.merge_service.create("测试合并版")
        appended = self.merge_service.append_range(
            edition_id,
            source_a,
            1,
            1,
        )
        # 返回值 1 表示范围 1-1 确实追加了一章。
        self.assertEqual(appended, 1)
        self.merge_service.compose(
            edition_id,
            title="组合章节",
            source_positions=[(source_a, 2), (source_b, 1)],
        )

        # 第一次导出应包含三个来源正文片段。
        first_export = self.merge_service.export(edition_id)
        first_text = first_export.read_text(encoding="utf-8")
        self.assertIn("A1正文", first_text)
        self.assertIn("A2正文", first_text)
        self.assertIn("B1旧正文", first_text)

        # 执行“刷新覆盖”：同一个 chapter_id 改为新正文，不改合并映射。
        self.storage.save_completed_chapter(
            chapter_b1.id,
            title="B1",
            content="B1刷新正文",
        )
        # 第二次导出重新查询来源章节，因此应看到新正文并不再包含旧正文。
        second_text = self.merge_service.export(edition_id).read_text(
            encoding="utf-8"
        )
        self.assertIn("B1刷新正文", second_text)
        self.assertNotIn("B1旧正文", second_text)
