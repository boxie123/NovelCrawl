"""Storage 数据库行为测试。

这些测试关注持久化语义，而不是 SQL 的具体写法。只要外部可观察行为保持
正确，未来可以重构 SQL 而无需修改测试。
"""

from novelcrawl.models import CatalogueChapter
from novelcrawl.storage import sanitize_filename

from tests.helpers import StorageTestCase


class StorageTests(StorageTestCase):
    """验证建库、目录同步、状态队列、刷新保护和范围统计。"""

    def test_schema_version_and_source_upsert(self):
        """新库应为版本 1，同一目录 URL 再写入应更新而非重复创建。"""
        # 准备/执行：setUp 已创建并初始化数据库，此处直接读取版本标记。
        version = self.storage.connection.execute(
            "PRAGMA user_version"
        ).fetchone()[0]
        # assertEqual(actual, expected) 判断两个值相等；不等时测试失败并显示差异。
        self.assertEqual(version, 1)

        # 第一次写入创建来源，第二次用同一 URL 写入应命中 upsert 更新分支。
        first_id = self.create_source()
        second_id = self.storage.upsert_source(
            catalogue_url="https://example.com/book/",
            title="测试小说新标题",
            site_name="example",
            site_config_key="sudugu",
            output_name="不会覆盖已有输出名",
        )

        # 两次返回同一个主键，证明没有插入重复来源。
        self.assertEqual(first_id, second_id)
        # 标题应更新为第二次同步的值。
        self.assertEqual(
            self.storage.get_source(first_id).title,
            "测试小说新标题",
        )

    def test_catalogue_sync_preserves_completed_content(self):
        """网站目录改名后，同步不能清空已经下载成功的正文。"""
        # 准备：创建三章，把第一章保存为 completed。
        source_id = self.create_source()
        chapters = self.sync_three_chapters(source_id)
        first = self.storage.get_chapters_by_positions(source_id, [1])[0]
        self.storage.save_completed_chapter(
            first.id,
            title="第1章 新标题",
            content="完整正文",
        )

        # 构造第二版目录：URL 和位置不变，只把第一章目录标题改名。
        changed = [
            CatalogueChapter(
                url=chapter.url,
                title=(
                    "第1章 目录改名"
                    if chapter.position == 1
                    else chapter.title
                ),
                normalized_title=(
                    "第1章 目录改名"
                    if chapter.position == 1
                    else chapter.normalized_title
                ),
                position=chapter.position,
            )
            for chapter in chapters
        ]
        self.storage.sync_catalogue(source_id, changed)

        # 执行后重新从数据库读取，而不是检查旧 Python 对象。
        updated = self.storage.get_chapter(first.id)
        # 三个断言共同保证：状态和正文保留，允许目录标题更新。
        self.assertEqual(updated.status, "completed")
        self.assertEqual(updated.content, "完整正文")
        self.assertEqual(updated.catalogue_title, "第1章 目录改名")

    def test_queue_prioritizes_pending_before_failed(self):
        """续传队列应先处理新 pending 章节，再重试 failed 章节。"""
        # 准备：三章初始均为 pending，再把目录位置 2 标记为 failed。
        source_id = self.create_source()
        self.sync_three_chapters(source_id)
        second = self.storage.get_chapters_by_positions(source_id, [2])[0]
        self.storage.save_failed_chapter(
            second.id,
            error_type="timeout",
            error_message="超时",
        )

        queue = self.storage.get_download_queue(source_id)
        # 列表推导式只取 position，便于直观看出实际顺序为 1、3、2。
        self.assertEqual([chapter.position for chapter in queue], [1, 3, 2])

    def test_refresh_failure_preserves_completed_content(self):
        """刷新已有正文失败时应保留旧正文，仅记录最新错误。"""
        # 准备：先保存一份可用旧正文。
        source_id = self.create_source()
        self.sync_three_chapters(source_id)
        chapter = self.storage.get_chapters_by_positions(source_id, [1])[0]
        self.storage.save_completed_chapter(
            chapter.id,
            title="第1章",
            content="旧正文",
        )

        # 执行：preserve_completed=True 模拟“手动刷新失败”而非普通下载失败。
        self.storage.save_failed_chapter(
            chapter.id,
            error_type="timeout",
            error_message="刷新失败",
            preserve_completed=True,
        )

        refreshed = self.storage.get_chapter(chapter.id)
        # 状态和正文未降级，但错误信息仍被保存，便于用户排查刷新失败。
        self.assertEqual(refreshed.status, "completed")
        self.assertEqual(refreshed.content, "旧正文")
        self.assertEqual(refreshed.last_error, "刷新失败")

    def test_progress_can_be_scoped_by_start_position(self):
        """start_position 之前的章节不应影响本次进度统计。"""
        # 准备：位置 1 失败，位置 2、3 仍为 pending。
        source_id = self.create_source()
        self.sync_three_chapters(source_id)
        first = self.storage.get_chapters_by_positions(source_id, [1])[0]
        self.storage.save_failed_chapter(
            first.id,
            error_type="timeout",
            error_message="范围外失败",
        )

        # 执行：从位置 2 开始统计，因此位置 1 的 failed 应完全排除。
        progress = self.storage.get_progress(
            source_id,
            start_position=2,
        )
        # 范围内总共两章，均为 pending，failed 为 0。
        self.assertEqual(progress.total, 2)
        self.assertEqual(progress.pending, 2)
        self.assertEqual(progress.failed, 0)

    def test_windows_reserved_device_names_are_made_safe(self):
        """Windows 设备别名即使大小写不同或带扩展名也必须转成普通名称。"""
        # 字典左侧是输入，右侧是期望结果。subTest 会把每一组数据作为独立
        # 子案例报告；某一组失败时仍会继续检查其余组，便于一次看全问题。
        cases = {
            "CON": "_CON",
            "con.txt": "_con.txt",
            "AUX": "_AUX",
            "nul": "_nul",
            "COM1": "_COM1",
            "lpt9.log": "_lpt9.log",
            "COM¹.txt": "_COM¹.txt",
            "普通小说": "普通小说",
        }
        for original, expected in cases.items():
            with self.subTest(original=original):
                self.assertEqual(sanitize_filename(original), expected)
