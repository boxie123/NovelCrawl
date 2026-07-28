"""下载服务的异步流程测试。

真实 ``DownloadService`` 会启动 Chromium 并访问网站。单元测试不应依赖
网络，因此下面定义几个模拟对象（fake）：它们拥有服务所需的同名方法，但
直接返回可预测数据。这样测试的是“服务如何编排结果”，而不是 Playwright。
"""

import asyncio
from contextlib import asynccontextmanager

from novelcrawl.config import AppConfig
from novelcrawl.exporter import TxtExporter
from novelcrawl.models import CatalogueChapter, FetchResult
from novelcrawl.services import DownloadService

from tests.helpers import StorageTestCase


class FakeCrawler:
    """模拟单章抓取器：第二章抛异常，其他章节成功。"""

    async def fetch_chapter(self, chapter):
        # async def 使它和真实抓取器接口一致，调用时仍需 await。
        if chapter.position == 2:
            raise RuntimeError("模拟单章异常")
        return FetchResult(
            chapter_id=chapter.id,
            title=chapter.normalized_title or "",
            content="第{}章正文".format(chapter.position),
        )


class CatalogueCrawler(FakeCrawler):
    """模拟完整下载所需的目录与正文接口，全程不访问网络。"""

    async def fetch_catalogue(self, catalogue_url):
        """返回三章固定目录；catalogue_url 参数只为保持真实接口一致。"""
        return (
            "下载测试",
            [
                CatalogueChapter(
                    url="https://www.sudugu.org/chapter/{}".format(position),
                    title="第{}章".format(position),
                    normalized_title="第{}章".format(position),
                    position=position,
                )
                for position in range(1, 4)
            ],
        )

    async def fetch_chapter(self, chapter):
        """所有章节都成功，正文包含目录位置便于后续断言。"""
        return FetchResult(
            chapter_id=chapter.id,
            title=chapter.normalized_title or "",
            content="第{}章正文".format(chapter.position),
        )


class CountingExporter(TxtExporter):
    """在真实导出功能外增加调用计数，用于防止每批重复导出。"""

    def __init__(self, *args, **kwargs):
        # super() 调用父类 TxtExporter 的初始化，保留真实导出能力。
        super().__init__(*args, **kwargs)
        self.source_export_count = 0

    def export_source(self, source_id, *, start_position=None):
        # 每次服务触发导出先加一，再委托父类真正写临时 TXT。
        self.source_export_count += 1
        return super().export_source(
            source_id,
            start_position=start_position,
        )


class SessionlessDownloadService(DownloadService):
    """用 CatalogueCrawler 替换真实 Playwright 会话的测试服务。"""

    @asynccontextmanager
    async def _crawler_session(self, site_config):
        """模拟 async with 所需的异步上下文管理器。

        ``yield`` 前相当于进入会话，``yield`` 后相当于退出会话。本 fake 没有
        浏览器资源需要关闭，所以只需提供抓取器。
        """
        yield CatalogueCrawler()


class DownloadServiceTests(StorageTestCase):
    """验证异常隔离、start-position 语义和单次导出约束。"""

    def test_single_chapter_exception_does_not_cancel_batch(self):
        """一章抛出意外异常时，同批其他章节仍应成功入库。"""
        # 准备：创建三章 pending 队列。
        source_id = self.create_source()
        self.sync_three_chapters(source_id)
        chapters = self.storage.get_download_queue(source_id)
        # 使用测试配置：不等待批次，单章最多尝试两次，所有输出放临时目录。
        config = AppConfig(
            database_path=self.root / "unused.db",
            source_export_dir=self.root / "exports" / "sources",
            merged_export_dir=self.root / "exports" / "merged",
            batch_size=3,
            batch_wait_seconds=0,
            max_attempts_per_run=2,
        )
        exporter = TxtExporter(
            self.storage,
            config.source_export_dir,
            config.merged_export_dir,
        )
        service = DownloadService(self.storage, exporter, config)

        # 执行：_run_batch 是 async 函数。普通 unittest 测试方法不是 async，
        # 因此使用 asyncio.run 创建事件循环并等待它执行完成。
        # assertLogs 临时接管指定 logger，并收集 ERROR 级别日志。FakeCrawler
        # 抛出的 RuntimeError 属于 unknown，应留下包含异常信息的堆栈日志。
        with self.assertLogs("novelcrawl.services", level="ERROR") as logs:
            asyncio.run(
                service._run_batch(
                    FakeCrawler(),
                    chapters,
                    preserve_completed=False,
                )
            )
        self.assertIn("模拟单章异常", "\n".join(logs.output))

        # 验证：位置 1、3 完成，位置 2 最终失败；总数仍为三章。
        progress = self.storage.get_progress(source_id)
        self.assertEqual(progress.completed, 2)
        self.assertEqual(progress.failed, 1)
        # 再读取失败章节，验证异常被转换成可持久化的 unknown 错误。
        failed = self.storage.get_chapters_by_positions(source_id, [2])[0]
        self.assertEqual(failed.last_error_type, "unknown")
        self.assertIn("模拟单章异常", failed.last_error)
        self.assertIn("logs/novelcrawl.log", failed.last_error)

    def test_download_exports_once_and_respects_start_position(self):
        """多批下载应只导出一次，且位置 1 不进入位置 2 起始的 TXT。"""
        # 准备：batch_size=1 会把位置 2、3 拆成两个批次。如果实现错误地
        # 每批导出，CountingExporter 的计数会变成 2。
        config = AppConfig(
            database_path=self.root / "unused.db",
            source_export_dir=self.root / "exports" / "sources",
            merged_export_dir=self.root / "exports" / "merged",
            batch_size=1,
            batch_wait_seconds=0,
            max_attempts_per_run=1,
        )
        exporter = CountingExporter(
            self.storage,
            config.source_export_dir,
            config.merged_export_dir,
        )
        service = SessionlessDownloadService(
            self.storage,
            exporter,
            config,
        )

        # 执行完整 download_url 流程，但浏览器会话已被 fake 替换。
        source_id = asyncio.run(
            service.download_url(
                "https://www.sudugu.org/book/",
                start_position=2,
            )
        )

        # assertEqual(..., 1) 是对“整次任务只导出一次”的直接回归保护。
        self.assertEqual(exporter.source_export_count, 1)
        progress = self.storage.get_progress(
            source_id,
            start_position=2,
        )
        # 从位置 2 统计时总数应为 2，且两章均完成。
        self.assertEqual(progress.total, 2)
        self.assertEqual(progress.completed, 2)

        source = self.storage.get_source(source_id)
        output_path = config.source_export_dir / "{}.txt".format(
            source.output_name
        )
        # pathlib.read_text 读取实际生成的 UTF-8 文件，验证用户最终看到的内容。
        text = output_path.read_text(encoding="utf-8")
        self.assertNotIn("第1章", text)
        self.assertIn("第2章", text)
        self.assertIn("第3章", text)
