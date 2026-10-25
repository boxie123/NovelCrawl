"""有界内存任务队列；一个消费者管理抓取、合并和导出。"""

import asyncio
import copy
import hashlib
import json
import logging
import time
import uuid
from dataclasses import replace
from playwright.async_api import Error as PlaywrightError

from novelcrawl.control import CleanupError, RunControl, RunStopped
from novelcrawl.errors import InvalidChapterSpec, SiteConfigNotFound
from novelcrawl.exporter import TxtExporter
from novelcrawl.services import DownloadService
from novelcrawl.site_config import match_site_config
from novelcrawl.storage import Storage
from novelcrawl.storage_gateway import StorageGateway
from novelcrawl.urls import normalize_url
from novelcrawl.validation import parse_position_spec

logger = logging.getLogger(__name__)
TERMINAL = {'succeeded', 'completed_with_errors', 'failed', 'cancelled'}


class TaskConflict(ValueError):
    pass


class TaskManager:
    def __init__(self, config, *, service_factory=DownloadService):
        self.config = config
        self.gateway = StorageGateway(config.database_path)
        self.service_factory = service_factory
        self.queue = asyncio.Queue(maxsize=100)
        self.records = {}
        self.requests = {}
        self.reservations = set()
        self.submit_lock = asyncio.Lock()
        self.active = None
        self.control = None
        self.service = None
        self.worker = None
        self.stopping = False
        self.closing = False
        self.cleanup_failed = False

    async def start(self):
        await self.gateway.open()
        self.worker = asyncio.create_task(self._consume(), name='novel-task-worker')

    def snapshot(self, task_id):
        return copy.deepcopy(self.records[task_id])

    def list_tasks(self):
        return [self.snapshot(key) for key in reversed(self.records)]

    async def _prepare(self, request):
        data = request.model_dump()
        kind = request.kind
        if kind == 'download':
            data['url'] = normalize_url(request.url)
            if match_site_config(data['url']) is None:
                raise SiteConfigNotFound('目录 URL 未匹配支持的站点')
            key = 'source:'+data['url']
        elif kind in ('resume', 'refresh', 'export_source'):
            source = await self.gateway.call('get_source', request.source_id)
            key = 'source:'+source.catalogue_url
            if kind == 'refresh':
                if request.chapter_ids is not None:
                    ids = list(dict.fromkeys(request.chapter_ids))
                    await self.gateway.call('chapters_by_ids', ids, request.source_id)
                else:
                    positions = None if request.all else parse_position_spec(request.chapters)
                    ids = await self.gateway.call('chapter_ids', request.source_id, positions)
                data['chapter_ids'] = ids
        elif kind == 'merge_create':
            editions = await self.gateway.call('list_merged_editions')
            if any(edition.name == request.name for edition in editions):
                raise TaskConflict('该名称的合并版本已存在')
            key = 'edition-name:'+request.name
        else:
            await self.gateway.call('get_merged_edition', request.edition_id)
            key = 'edition:'+str(request.edition_id)
            if kind == 'merge_append':
                positions = parse_position_spec(request.chapters)
                if positions != list(range(positions[0], positions[-1]+1)):
                    raise InvalidChapterSpec('追加范围必须连续')
                data['chapter_ids'] = await self.gateway.call('chapter_ids', request.source_id, positions)
            elif kind == 'merge_compose':
                await self.gateway.call('chapters_by_ids', request.chapter_ids)
        return data, key

    async def submit(self, request):
        """请求标识重放返回同一任务；入队后只保存稳定章节 ID。"""
        payload = request.model_dump(mode='json')
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        async with self.submit_lock:
            if request.request_id in self.requests:
                previous, fingerprint = self.requests[request.request_id]
                if fingerprint != digest:
                    raise TaskConflict('请求标识已用于不同的操作')
                return self.snapshot(previous)
            if self.stopping or self.closing or self.cleanup_failed:
                raise TaskConflict('正在停止或资源清理异常，暂不接受新任务')
            data, key = await self._prepare(request)
            if self.stopping or self.closing or self.cleanup_failed:
                raise TaskConflict('正在停止，暂不接受新任务')
            if key in self.reservations:
                raise TaskConflict('该来源或合并版本已有任务，请等待结束')
            if self.queue.full():
                raise TaskConflict('队列已满（最多 100 项）')
            task_id = uuid.uuid4().hex
            summary = {k: v for k, v in payload.items() if k not in ('chapter_ids', 'request_id')}
            if 'chapter_ids' in data:
                summary['selected_count'] = len(data['chapter_ids'])
            record = dict(id=task_id, kind=request.kind, status='queued', phase='queued',
                          parameters=summary, source_id=data.get('source_id'),
                          created_at=time.time(), started_at=None, ended_at=None,
                          total=0, processed=0, succeeded=0, failed=0, error=None, result=None)
            self.records[task_id] = record
            self.requests[request.request_id] = (task_id, digest)
            self.reservations.add(key)
            self.queue.put_nowait((task_id, data, key))
            return self.snapshot(task_id)

    def request_stop_all(self):
        """幂等停止：先关闭调度，再中断网络；不取消负责事务收尾的主协程。"""
        self.stopping = True
        while not self.queue.empty():
            task_id, _, key = self.queue.get_nowait()
            self.records[task_id].update(status='cancelled', phase='cancelled', ended_at=time.time())
            self.reservations.discard(key)
            self.queue.task_done()
        if self.active is not None:
            self.records[self.active]['status'] = 'stopping'
            self.control.stop()
            if self.service is not None:
                self.service.request_stop()
        elif not self.closing and not self.cleanup_failed:
            self.stopping = False
        self._trim()

    async def close(self):
        self.closing = True
        self.request_stop_all()
        await self.queue.join()
        if self.worker:
            # 此时消费者只在等待队列，已无 Playwright 调用。
            self.worker.cancel()
            try:
                await self.worker
            except asyncio.CancelledError:
                pass
        try:
            if self.cleanup_failed and self.service is not None and self.service.session is not None:
                await self.service.session.close()
        finally:
            await self.gateway.close()

    def _trim(self):
        finished = [key for key, record in self.records.items() if record['status'] in TERMINAL]
        for key in finished[:-100]:
            del self.records[key]
            self.requests = {r: pair for r, pair in self.requests.items() if pair[0] != key}

    async def _consume(self):
        while True:
            task_id, data, key = await self.queue.get()
            self.active = task_id
            self.control = RunControl()
            record = self.records[task_id]
            record.update(status='running', phase='starting', started_at=time.time())
            try:
                result = await self._run(data, record)
                if not self.control.commit_reserved:
                    self.control.check()
                record.update(result=result, status='completed_with_errors' if record['failed'] else 'succeeded')
            except RunStopped:
                record['status'] = 'cancelled'
            except Exception as exc:
                logger.exception('任务执行失败：%s', task_id)
                if isinstance(exc, CleanupError):
                    self.cleanup_failed = True
                record['status'] = 'failed'
                record['error'] = str(exc).encode('utf-8')[:4096].decode('utf-8', errors='ignore')
                if self.control.stopped.is_set() and isinstance(exc, PlaywrightError):
                    record['status'] = 'cancelled'
            finally:
                record.update(ended_at=time.time(), phase=record['status'], current_chapters=[], wait_until=None)
                self.reservations.discard(key)
                self.active = None
                if not self.cleanup_failed:
                    self.service = None
                self.control = None
                self.queue.task_done()
                if self.cleanup_failed:
                    self.request_stop_all()
                elif not self.closing:
                    self.stopping = False
                self._trim()
                # 不让已结束任务持有章节选择或正文。
                data = None

    async def _run(self, data, record):
        kind = data['kind']
        control = self.control
        control.check()
        if kind in ('download', 'resume', 'refresh'):
            config = replace(self.config, batch_size=data['concurrency'])
            self.service = self.service_factory(self.gateway, config, control=control,
                                               report=lambda **fields: record.update(fields))
            if kind == 'download':
                source_id = await self.service.download_url(data['url'], start_position=data['start_position'])
            elif kind == 'resume':
                source_id = await self.service.resume_source(data['source_id'], start_position=data['start_position'])
            else:
                source_id = await self.service.refresh_source(data['source_id'], chapter_ids=data['chapter_ids'])
            control.check()
            record['source_id'] = source_id
            record['phase'] = 'exporting'
            path = await asyncio.to_thread(self._export, 'source', source_id, control, data.get('start_position'))
            return {'source_id': source_id, 'path': str(path), 'download_url': f'/api/exports/source/{source_id}/file'}
        if kind in ('export_source', 'export_merged'):
            export_type = 'source' if kind == 'export_source' else 'merged'
            object_id = data['source_id'] if export_type == 'source' else data['edition_id']
            record['phase'] = 'exporting'
            path = await asyncio.to_thread(self._export, export_type, object_id, control)
            return {'path': str(path), 'download_url': f'/api/exports/{export_type}/{object_id}/file'}
        record['phase'] = 'merging'
        if kind == 'merge_create':
            edition_id = await self.gateway.call('create_merged_edition', data['name'],
                description=data['description'], output_name=data['output_name'], control=control)
            return {'edition_id': edition_id}
        count = await self.gateway.call('merge_from_ids', data['edition_id'], data['chapter_ids'],
            compose=kind == 'merge_compose', title=data.get('title'), control=control)
        return {'edition_id': data['edition_id'], 'count': count}

    def _export(self, kind, object_id, control, start_position=None):
        """完整导出生命周期留在同一工作线程，以读事务提供一致快照。"""
        control.check()
        with Storage(self.config.database_path, read_only=True) as storage:
            storage.connection.execute('BEGIN')
            exporter = TxtExporter(storage, self.config.source_export_dir,
                                   self.config.merged_export_dir, control=control)
            if kind == 'source':
                return exporter.export_source(object_id, start_position=start_position)
            return exporter.export_merged_edition(object_id)
