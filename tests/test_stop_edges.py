"""停止发生在初始化、解析、等待和提交边界的回归。"""
import asyncio
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from novelcrawl.browser_session import BrowserSession
from novelcrawl.control import CleanupError, RunControl, RunStopped
from novelcrawl.crawler import NovelCrawler
from novelcrawl.models import ChapterRecord, SiteConfig, CatalogueChapter
from novelcrawl.storage import Storage
from novelcrawl.task_manager import TaskManager, TaskConflict
from tests.test_crawler import FakeContext, PaginatedChapterPage
from tests.test_download_service import SessionlessDownloadService
from tests.test_gui_backend import configuration, populate, request

SITE = SiteConfig('test', ('example.com',), 'ul li', '.page a', 'h1', '.content', '.page a')


class StopBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_late_browser_is_closed_before_context_or_navigation(self):
        token = RunControl(); session = BrowserSession(token)
        entered, release = asyncio.Event(), asyncio.Event()
        browser = SimpleNamespace(close=AsyncMock(), is_connected=lambda: False, new_context=AsyncMock())
        async def launch(**kwargs):
            entered.set(); await release.wait(); return browser
        playwright = SimpleNamespace(chromium=SimpleNamespace(launch=launch), stop=AsyncMock())
        starter = SimpleNamespace(start=AsyncMock(return_value=playwright))
        with patch('novelcrawl.browser_session.async_playwright', return_value=starter):
            task = asyncio.create_task(session.start(SimpleNamespace(), lambda ua: ua, None))
            await entered.wait()
            token.stop(); session.request_stop(); release.set()
            with self.assertRaises(RunStopped):
                await task
            await session.close()
        browser.new_context.assert_not_awaited()
        browser.close.assert_awaited_once()
        playwright.stop.assert_awaited_once()

    async def test_close_error_is_not_reported_as_clean_cancellation(self):
        token=RunControl(); token.stop()
        session=BrowserSession(token)
        session.browser=SimpleNamespace(close=AsyncMock(side_effect=RuntimeError('close failed')), is_connected=lambda: False)
        session.playwright=SimpleNamespace(stop=AsyncMock())
        with self.assertRaises(CleanupError):
            await session.close()

    async def test_stop_wakes_navigation_retry_wait_without_new_request(self):
        token=RunControl(); waiting=asyncio.Event()
        original=token.wait
        async def wait(seconds):
            waiting.set(); await original(seconds)
        token.wait=wait
        crawler=NovelCrawler(None,SITE,control=token)
        page=SimpleNamespace(goto=AsyncMock(return_value=SimpleNamespace(status=503)))
        task=asyncio.create_task(crawler._page_goto(page,'https://example.com/chapter/1'))
        await waiting.wait(); token.stop()
        with self.assertRaises(RunStopped):
            await asyncio.wait_for(task,1)
        self.assertEqual(page.goto.await_count,1)

    async def test_late_parse_does_not_continue_catalogue_or_body_pagination(self):
        for catalogue in (True,False):
            with self.subTest(catalogue=catalogue):
                token=RunControl(); page=PaginatedChapterPage()
                crawler=NovelCrawler(FakeContext(page),SITE,control=token)
                original=crawler._parse
                async def parse(function,*args,**kwargs):
                    value=await original(function,*args,**kwargs)
                    if function.__name__ in ('find_next_page_href','<lambda>'):
                        token.stop()
                        token.check()
                    return value
                crawler._parse=parse
                chapter=ChapterRecord(1,1,'https://example.com/chapter/1',1,'第一章','第一章','pending',None,None,None)
                with self.assertRaises(RunStopped):
                    if catalogue:
                        await crawler.fetch_catalogue(chapter.url)
                    else:
                        await crawler.fetch_chapter(chapter)
                self.assertEqual(len(page.navigation_records),1)

    async def test_stop_during_parser_thread_discards_late_result(self):
        import threading
        token=RunControl(); entered=threading.Event(); release=threading.Event()
        crawler=NovelCrawler(None,SITE,control=token)
        def parse():
            entered.set(); release.wait(3); return 'late result'
        task=asyncio.create_task(crawler._parse(parse))
        await asyncio.to_thread(entered.wait,3)
        token.stop(); release.set()
        with self.assertRaises(RunStopped):
            await task

    async def test_batch_wait_stop_and_cleanup_failure_blocking(self):
        with tempfile.TemporaryDirectory() as directory:
            config=replace(configuration(Path(directory)),batch_wait_seconds=60)
            source, ids=populate(config)
            manager=TaskManager(config,service_factory=SessionlessDownloadService)
            await manager.start()
            try:
                task=await manager.submit(request('resume',source_id=source,concurrency=1))
                for _ in range(300):
                    if manager.snapshot(task['id'])['phase']=='waiting': break
                    await asyncio.sleep(.01)
                self.assertEqual(manager.snapshot(task['id'])['phase'],'waiting')
                manager.request_stop_all()
                await asyncio.wait_for(manager.queue.join(),1)
                self.assertEqual((await manager.gateway.call('get_progress',source)).completed,1)
                self.assertFalse(config.source_export_dir.exists())
                class BrokenService(SessionlessDownloadService):
                    async def download_url(self,*args,**kwargs):
                        raise CleanupError('simulated cleanup failure')
                manager.service_factory=BrokenService
                failed=await manager.submit(request('resume',source_id=source))
                await manager.queue.join()
                self.assertEqual(manager.snapshot(failed['id'])['status'],'failed')
                with self.assertRaises(TaskConflict):
                    await manager.submit(request('merge_create',name='blocked'))
            finally:
                await manager.close()


class TransactionEdges(unittest.TestCase):
    def test_merge_mid_transaction_rolls_back_and_reordered_source_keeps_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            config=configuration(Path(directory)); source,ids=populate(config)
            with Storage(config.database_path) as storage:
                edition=storage.create_merged_edition('test')
                class StopAfterFirstPart(RunControl):
                    def __init__(self):
                        super().__init__(); self.calls=0
                    def check(self):
                        self.calls+=1
                        if self.calls==3: raise RunStopped('mid transaction')
                with self.assertRaises(RunStopped):
                    storage.merge_from_ids(edition,ids,control=StopAfterFirstPart())
                self.assertEqual(storage.edition_page(edition)['total'],0)
                storage.sync_catalogue(source,[CatalogueChapter(f'https://www.sudugu.org/chapter/{i}',str(i),str(i),position)
                    for position,i in enumerate((3,2,1),1)])
                storage.merge_from_ids(edition,ids)
                items=storage.edition_page(edition)['items']
                self.assertEqual([row['parts'][0]['id'] for row in items],ids)
                self.assertEqual([row['parts'][0]['position'] for row in items],[3,2,1])
