"""任务/HTTP/线程/取消回归，所有数据位于临时目录。"""

import asyncio
import os
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from pydantic import TypeAdapter, ValidationError

from novelcrawl.config import AppConfig
from novelcrawl.control import RunControl, RunStopped
from novelcrawl.errors import InvalidChapterSpec
from novelcrawl.exporter import TxtExporter
from novelcrawl.models import CatalogueChapter, FetchResult
from novelcrawl.storage import Storage
from novelcrawl.storage_gateway import StorageGateway, read_database
from novelcrawl.task_manager import TaskConflict, TaskManager
from novelcrawl.task_models import TaskRequest
from novelcrawl.validation import parse_position_spec
from novelcrawl.web import create_app
from tests.test_download_service import SessionlessDownloadService, WarmupDownloadService, FakeCrawler


ADAPTER = TypeAdapter(TaskRequest)


def request(kind, **kwargs):
    import uuid
    return ADAPTER.validate_python(dict(kind=kind, request_id=uuid.uuid4().hex, **kwargs))


def configuration(root):
    return AppConfig(database_path=root/'test.db', source_export_dir=root/'sources',
                     merged_export_dir=root/'merged', log_path=root/'logs'/'test.log',
                     batch_wait_seconds=0, max_attempts_per_run=1)


def populate(config):
    with Storage(config.database_path) as storage:
        source = storage.upsert_source(catalogue_url='https://www.sudugu.org/book/', title='测试书',
            site_name='sudugu', site_config_key='sudugu', output_name='测试书')
        storage.sync_catalogue(source, [CatalogueChapter(f'https://www.sudugu.org/chapter/{i}',
            f'第{i}章', f'第{i}章', i) for i in range(1, 4)])
        return source, storage.chapter_ids(source)


class ValidationTests(unittest.TestCase):
    def test_ranges_and_invalid_ranges(self):
        self.assertEqual(parse_position_spec('1-3,5,8-9'), [1,2,3,5,8,9])
        for value in ('3-1', '', '0', '1,,2', '1-999999999'):
            with self.subTest(value=value), self.assertRaises(InvalidChapterSpec):
                parse_position_spec(value)

    def test_network_concurrency_and_refresh_selection(self):
        for kind, data in [('download', {'url':'https://www.sudugu.org/book/'}),
                           ('resume', {'source_id':1}), ('refresh', {'source_id':1,'all':True})]:
            for concurrency in (0, -1, 6, 'three', True):
                with self.subTest(kind=kind, concurrency=concurrency), self.assertRaises(ValidationError):
                    request(kind, concurrency=concurrency, **data)
        with self.assertRaises(ValidationError):
            request('refresh', source_id=1, all=True, chapters='1')
        with self.assertRaises(ValidationError):
            request('refresh', source_id=1)


class AsyncBackendTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = configuration(self.root)
        self.source, self.ids = populate(self.config)
        self.manager = TaskManager(self.config, service_factory=SessionlessDownloadService)
        await self.manager.start()

    async def asyncTearDown(self):
        await self.manager.close()
        self.temp.cleanup()

    async def test_gateway_owns_connection_and_confirms_transaction(self):
        threads = []
        def inspect_connection(storage):
            threads.append(threading.get_ident())
            storage.connection.execute('SELECT 1').fetchone()
        await self.manager.gateway._execute(inspect_connection, self.manager.gateway.storage)
        await self.manager.gateway._execute(inspect_connection, self.manager.gateway.storage)
        self.assertEqual(threads[0], threads[1])
        self.assertNotEqual(threads[0], threading.get_ident())
        started, release = threading.Event(), threading.Event()
        def write():
            started.set()
            release.wait(3)
            self.manager.gateway.storage.save_completed_chapter(self.ids[0], title='已提交', content='完整正文')
        task = asyncio.create_task(self.manager.gateway._execute(write))
        await asyncio.to_thread(started.wait, 3)
        task.cancel()
        await asyncio.sleep(0)
        self.assertFalse(task.done())
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        chapter = await self.manager.gateway.call('get_chapter', self.ids[0])
        self.assertEqual(chapter.content, '完整正文')

    async def test_normal_run_exports_once_and_preserves_scope(self):
        with patch.object(self.manager, '_export', wraps=self.manager._export) as export:
            task = await self.manager.submit(request('download', url='https://www.sudugu.org/book/', start_position=2, concurrency=1))
            await self.manager.queue.join()
            self.assertEqual(export.call_count, 1)
        result = self.manager.snapshot(task['id'])
        self.assertEqual(result['status'], 'succeeded')
        self.assertEqual((result['total'],result['processed']), (2,2))
        text = Path(result['result']['path']).read_text(encoding='utf-8')
        self.assertNotIn('第1章', text)
        self.assertIn('第2章', text)
        # 手动导出不继承上次任务的范围。
        task = await self.manager.submit(request('export_source', source_id=self.source))
        await self.manager.queue.join()
        self.assertIn('第1章', Path(self.manager.snapshot(task['id'])['result']['path']).read_text(encoding='utf-8'))

    async def test_empty_queue_still_exports_once(self):
        await self.manager.submit(request('resume', source_id=self.source))
        await self.manager.queue.join()
        with patch.object(self.manager, '_export', wraps=self.manager._export) as export:
            task = await self.manager.submit(request('resume', source_id=self.source))
            await self.manager.queue.join()
            self.assertEqual(export.call_count, 1)
        self.assertEqual(self.manager.snapshot(task['id'])['total'], 0)

    async def test_completed_chapter_saved_while_peer_blocked_then_stop_no_export(self):
        ready = asyncio.Event()
        class BlockingService(SessionlessDownloadService):
            async def _safe_fetch(inner, crawler, chapter):
                if chapter.position == 2:
                    ready.set()
                    await inner.control.wakeup.wait()
                    return None
                return await super()._safe_fetch(crawler, chapter)
        self.manager.service_factory = BlockingService
        with patch.object(self.manager, '_export', wraps=self.manager._export) as export:
            task = await self.manager.submit(request('resume', source_id=self.source))
            await ready.wait()
            for _ in range(100):
                chapter = await self.manager.gateway.call('get_chapter', self.ids[0])
                if chapter.status == 'completed':
                    break
                await asyncio.sleep(.01)
            self.assertEqual(chapter.status, 'completed')
            self.manager.request_stop_all()
            self.manager.request_stop_all()
            await self.manager.queue.join()
            self.assertEqual(export.call_count, 0)
        self.assertEqual(self.manager.snapshot(task['id'])['status'], 'cancelled')
        self.assertEqual((await self.manager.gateway.call('get_chapter', self.ids[1])).status, 'pending')

    async def test_request_replay_duplicate_source_and_queued_cancellation(self):
        gate = asyncio.Event()
        class BlockingService(SessionlessDownloadService):
            async def download_url(inner, *args, **kwargs):
                gate.set()
                await inner.control.wakeup.wait()
                inner.control.check()
        self.manager.service_factory = BlockingService
        payload = request('resume', source_id=self.source)
        first = await self.manager.submit(payload)
        replay = await self.manager.submit(payload)
        self.assertEqual(first['id'], replay['id'])
        await gate.wait()
        with self.assertRaises(TaskConflict):
            await self.manager.submit(request('download', url='https://www.sudugu.org/book/#x'))
        queued = await self.manager.submit(request('merge_create', name='排队版本'))
        self.manager.request_stop_all()
        await self.manager.queue.join()
        self.assertEqual(self.manager.snapshot(queued['id'])['status'], 'cancelled')
        self.assertEqual(await self.manager.gateway.call('list_merged_editions'), [])

    async def test_merge_stable_ids_preserves_order_and_duplicates(self):
        created = await self.manager.submit(request('merge_create', name='合并版本', description='说明', output_name='安全文件名'))
        await self.manager.queue.join()
        edition = self.manager.snapshot(created['id'])['result']['edition_id']
        task = await self.manager.submit(request('merge_compose', edition_id=edition,
                                                 chapter_ids=[self.ids[1], self.ids[0], self.ids[1]], title='完整章'))
        await self.manager.queue.join()
        self.assertEqual(self.manager.snapshot(task['id'])['status'], 'succeeded')
        detail = await self.manager.gateway.call('edition_page', edition)
        self.assertEqual([p['id'] for p in detail['items'][0]['parts']], [self.ids[1],self.ids[0],self.ids[1]])
        self.assertEqual(detail['edition']['description'], '说明')
        token = RunControl(); token.stop()
        with self.assertRaises(RunStopped):
            await self.manager.gateway.call('merge_from_ids', edition, self.ids, control=token)
        self.assertEqual((await self.manager.gateway.call('edition_page', edition))['total'], 1)

    async def test_refresh_failure_keeps_content_and_counts_this_run(self):
        for chapter_id in self.ids:
            await self.manager.gateway.call('save_completed_chapter', chapter_id, title='旧标题', content='旧正文')
        class FailedRefresh(WarmupDownloadService):
            async def _safe_fetch(inner, crawler, chapter):
                return FetchResult(chapter.id, '新标题', None, 'network', '模拟网络错误')
        self.manager.service_factory = FailedRefresh
        task = await self.manager.submit(request('refresh', source_id=self.source, chapter_ids=[self.ids[0]]))
        await self.manager.queue.join()
        result = self.manager.snapshot(task['id'])
        self.assertEqual((result['status'],result['total'],result['failed']), ('completed_with_errors',1,1))
        chapter = await self.manager.gateway.call('get_chapter', self.ids[0])
        self.assertEqual((chapter.status,chapter.content), ('completed','旧正文'))


    async def test_restart_keeps_database_but_does_not_restore_memory_queue(self):
        await self.manager.submit(request('resume',source_id=self.source))
        await self.manager.queue.join()
        self.assertTrue(self.manager.list_tasks())
        await self.manager.close()
        self.manager=TaskManager(self.config,service_factory=SessionlessDownloadService)
        await self.manager.start()
        self.assertEqual(self.manager.list_tasks(),[])
        self.assertTrue(self.manager.queue.empty())
        self.assertEqual((await self.manager.gateway.call('get_progress',self.source)).completed,3)

    async def test_stop_during_chapter_transaction_waits_for_commit_before_terminal(self):
        entered,release=threading.Event(),threading.Event()
        original=Storage.save_completed_chapter
        def save(storage,*args,**kwargs):
            entered.set(); release.wait(3); return original(storage,*args,**kwargs)
        with patch.object(Storage,'save_completed_chapter',save):
            task=await self.manager.submit(request('resume',source_id=self.source,concurrency=1))
            await asyncio.to_thread(entered.wait,3)
            self.manager.request_stop_all()
            await asyncio.sleep(.01)
            self.assertEqual(self.manager.snapshot(task['id'])['status'],'stopping')
            release.set(); await self.manager.queue.join()
        self.assertEqual(self.manager.snapshot(task['id'])['status'],'cancelled')
        self.assertEqual((await self.manager.gateway.call('get_chapter',self.ids[0])).status,'completed')
        self.assertFalse(self.config.source_export_dir.exists())

    async def test_catalogue_failure_and_cancelled_refresh_do_not_export(self):
        class BrokenCatalogue(SessionlessDownloadService):
            async def download_url(inner,*args,**kwargs):
                raise RuntimeError('catalogue failure')
        self.manager.service_factory=BrokenCatalogue
        with patch.object(self.manager,'_export',wraps=self.manager._export) as export:
            task=await self.manager.submit(request('resume',source_id=self.source))
            await self.manager.queue.join()
            self.assertEqual(self.manager.snapshot(task['id'])['status'],'failed')
            self.assertEqual(export.call_count,0)
        await self.manager.gateway.call('save_completed_chapter',self.ids[0],title='旧标题',content='旧正文')
        ready=asyncio.Event()
        class PausedRefresh(WarmupDownloadService):
            async def _safe_fetch(inner,*args):
                ready.set(); await inner.control.wakeup.wait(); return None
        self.manager.service_factory=PausedRefresh
        with patch.object(self.manager,'_export',wraps=self.manager._export) as export:
            task=await self.manager.submit(request('refresh',source_id=self.source,chapter_ids=[self.ids[0]]))
            await ready.wait(); self.manager.request_stop_all()
            await self.manager.queue.join()
            self.assertEqual(export.call_count,0)
            self.assertEqual(self.manager.snapshot(task['id'])['status'],'cancelled')
        chapter=await self.manager.gateway.call('get_chapter',self.ids[0])
        self.assertEqual((chapter.status,chapter.content),('completed','旧正文'))

    async def test_queue_limit_terminal_history_and_thread_lifecycle(self):
        entered=asyncio.Event()
        class Blocked(SessionlessDownloadService):
            async def download_url(inner,*args,**kwargs):
                entered.set(); await inner.control.wakeup.wait(); inner.control.check()
        self.manager.service_factory=Blocked
        await self.manager.submit(request('resume',source_id=self.source))
        await entered.wait()
        for i in range(100):
            await self.manager.submit(request('merge_create',name=f'queued-{i}'))
        with self.assertRaises(TaskConflict):
            await self.manager.submit(request('merge_create',name='overflow'))
        self.manager.request_stop_all(); await self.manager.queue.join()
        self.assertEqual(len(self.manager.list_tasks()),100)
        self.assertEqual(await self.manager.gateway.call('list_merged_editions'),[])
        # 记录创建、使用、关闭连接的线程，避免只验证调用线程。
        events=[]
        original_open, original_close=Storage.open,Storage.close
        def opened(storage):
            events.append(('open',threading.get_ident())); return original_open(storage)
        def closed(storage):
            events.append(('close',threading.get_ident())); return original_close(storage)
        gateway=StorageGateway(self.root/'other.db')
        with patch.object(Storage,'open',opened),patch.object(Storage,'close',closed):
            await gateway.open()
            await gateway._execute(lambda: events.append(('use',threading.get_ident())))
            await gateway.close()
        self.assertEqual([e[0] for e in events],['open','use','close'])
        self.assertEqual(len({e[1] for e in events}),1)
        self.assertNotEqual(events[0][1],threading.get_ident())


class ExportCancellationTests(unittest.TestCase):
    def test_cancel_keeps_old_file_and_removes_temporary(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'book.txt'; path.write_text('旧文件', encoding='utf-8')
            control = RunControl()
            def chunks():
                yield '部分新正文'
                control.stop()
                yield '后续正文'
            with self.assertRaises(RunStopped):
                TxtExporter._atomic_write(path, chunks(), control)
            self.assertEqual(path.read_text(encoding='utf-8'), '旧文件')
            self.assertEqual(list(Path(directory).glob('*.tmp')), [])

    def test_stop_after_replace_keeps_new_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'book.txt'; control = RunControl()
            real_replace = os.replace
            def commit(source, target):
                real_replace(source, target)
                control.stop()
            with patch('novelcrawl.exporter.os.replace', side_effect=commit):
                TxtExporter._atomic_write(path, ['新文件'], control)
            self.assertEqual(path.read_text(encoding='utf-8'), '新文件')

    def test_export_disk_failure_keeps_old_file_and_cleans_temporary(self):
        for operation in ('fsync','replace'):
            with self.subTest(operation=operation),tempfile.TemporaryDirectory() as directory:
                path=Path(directory)/'book.txt'; path.write_text('old',encoding='utf-8')
                with patch('novelcrawl.exporter.os.'+operation,side_effect=OSError('simulated disk failure')):
                    with self.assertRaises(OSError):
                        TxtExporter._atomic_write(path,['new'])
                self.assertEqual(path.read_text(encoding='utf-8'),'old')
                self.assertEqual(list(Path(directory).glob('*.tmp')),[])

    def test_readonly_missing_database_does_not_create(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'absent'/'book.db'
            with self.assertRaises(Exception):
                read_database(path, 'list_sources')
            self.assertFalse(path.parent.exists())


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.config = configuration(Path(self.temp.name))
        self.source, self.ids = populate(self.config)
        self.client = TestClient(create_app(self.config, testing=True,
            manager_factory=lambda config: TaskManager(config, service_factory=SessionlessDownloadService)))
        self.client.__enter__()
        self.origin = {'Origin':'http://testserver'}

    def tearDown(self):
        self.client.__exit__(None,None,None)
        self.temp.cleanup()

    def test_reading_pagination_validation_and_origin(self):
        response = self.client.get('/api/sources')
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()[0]['total'],3)
        rows = self.client.get(f'/api/sources/{self.source}/chapters?page_size=1&page=2').json()
        self.assertEqual(rows['items'][0]['position'],2)
        self.assertNotIn('content',rows['items'][0])
        self.assertEqual(self.client.get(f'/api/sources/{self.source}/chapters?page_size=201').status_code,422)
        self.assertEqual(self.client.get('/api/chapters/999999').status_code,404)
        payload = request('resume',source_id=self.source).model_dump()
        self.assertEqual(self.client.post('/api/tasks',json=payload).status_code,403)
        self.assertEqual(self.client.post('/api/tasks',json=payload,headers={'Origin':'https://evil.example'}).status_code,403)
        payload['concurrency']=6
        self.assertEqual(self.client.post('/api/tasks',json=payload,headers=self.origin).status_code,422)

    def test_preview_neighbors_stay_in_source_and_use_position_then_id(self):
        first=self.client.get(f'/api/chapters/{self.ids[0]}').json()
        self.assertIsNone(first['previous_id'])
        self.assertEqual(first['next_id'],self.ids[1])
        last=self.client.get(f'/api/chapters/{self.ids[-1]}').json()
        self.assertIsNone(last['next_id'])
        self.assertEqual(last['previous_id'],self.ids[1])
        with Storage(self.config.database_path) as storage:
            with storage.connection:
                storage.connection.execute('UPDATE chapters SET position=1 WHERE id=?',(self.ids[1],))
        middle=self.client.get(f'/api/chapters/{self.ids[1]}').json()
        self.assertEqual((middle['previous_id'],middle['next_id']),(self.ids[0],self.ids[2]))

    def test_task_acceptance_and_duplicate_replay(self):
        payload = request('resume',source_id=self.source).model_dump()
        first = self.client.post('/api/tasks',json=payload,headers=self.origin)
        self.assertEqual(first.status_code,202,first.text)
        second = self.client.post('/api/tasks',json=payload,headers=self.origin)
        self.assertEqual(first.json()['id'],second.json()['id'])
        altered = dict(payload, concurrency=1)
        self.assertEqual(self.client.post('/api/tasks',json=altered,headers=self.origin).status_code,409)
        self.assertEqual(self.client.post('/api/tasks/stop-all',headers=self.origin).status_code,202)

    def test_all_task_kinds_and_export_downloads(self):
        def complete(kind, **kwargs):
            response = self.client.post('/api/tasks', json=request(kind, **kwargs).model_dump(), headers=self.origin)
            self.assertEqual(response.status_code, 202, response.text)
            self.client.portal.call(self.client.app.state.manager.queue.join)
            result = self.client.get('/api/tasks/'+response.json()['id']).json()
            self.assertEqual(result['status'], 'succeeded', result)
            return result
        complete('download', url='https://www.sudugu.org/book/', start_position=2)
        complete('resume', source_id=self.source)
        with patch('tests.test_download_service.CatalogueCrawler.warm_up', new=AsyncMock(), create=True):
            for selection in ({'all':True}, {'chapters':'1-2'}, {'chapter_ids':[self.ids[2]]}):
                complete('refresh', source_id=self.source, **selection)
        source_export = complete('export_source', source_id=self.source)
        self.assertEqual(self.client.get(source_export['result']['download_url']).status_code, 200)
        edition=complete('merge_create', name='API 全流程', description='测试说明', output_name='测试输出')['result']['edition_id']
        complete('merge_append', edition_id=edition, source_id=self.source, chapters='1-2')
        complete('merge_compose', edition_id=edition, chapter_ids=[self.ids[2],self.ids[0],self.ids[2]], title='自定义章节')
        exported=complete('export_merged', edition_id=edition)
        self.assertIn('自定义章节',self.client.get(exported['result']['download_url']).text)
        self.assertEqual(self.client.get(f'/api/editions/{edition}').json()['description'],'测试说明')
        listing=self.client.get(f'/api/editions/{edition}/chapters').json()
        self.assertEqual([part['id'] for part in listing['items'][2]['parts']],[self.ids[2],self.ids[0],self.ids[2]])
        missing=request('resume',source_id=999999).model_dump()
        self.assertEqual(self.client.post('/api/tasks',json=missing,headers=self.origin).status_code,404)
