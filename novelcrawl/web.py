"""本机同源 API 与编译后的 Vue 静态资源。"""

import asyncio
import logging
import sqlite3
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from novelcrawl.config import DEFAULT_CONFIG
from novelcrawl.errors import ChapterNotFound, MergeEditionNotFound, NovelCrawlError, SourceNotFound
from novelcrawl.site_config import SITE_CONFIGS
from novelcrawl.storage_gateway import read_database
from novelcrawl.task_manager import TaskConflict, TaskManager
from novelcrawl.task_models import TaskRequest


def static_directory():
    packaged = Path(__file__).resolve().parent / 'static'
    return packaged if packaged.is_dir() else Path(__file__).resolve().parent.parent / 'frontend' / 'dist'


def create_app(config=DEFAULT_CONFIG, *, manager_factory=TaskManager, testing=False):
    """工厂不访问网络或建库；资源仅在 lifespan 内初始化。"""
    @asynccontextmanager
    async def lifespan(app):
        manager = manager_factory(config)
        app.state.manager = manager
        try:
            await manager.start()
            yield
        finally:
            await manager.close()

    app = FastAPI(title='NovelCrawl 本地 API', version='0.3.0', lifespan=lifespan,
                  docs_url=None, redoc_url=None)
    app.add_middleware(TrustedHostMiddleware,
                       allowed_hosts=['127.0.0.1', 'localhost'] + (['testserver'] if testing else []))

    @app.middleware('http')
    async def local_requests(request, call_next):
        if request.url.path.startswith('/api/'):
            origin = request.headers.get('origin')
            expected = f'{request.url.scheme}://{request.headers.get("host", "")}'
            if request.headers.get('sec-fetch-site') == 'cross-site' or (origin and origin != expected):
                return JSONResponse({'error': {'message': '仅允许本机同源请求'}}, status_code=403)
            if request.method not in ('GET', 'HEAD', 'OPTIONS'):
                if origin != expected:
                    return JSONResponse({'error': {'message': '写操作必须包含同源 Origin'}}, status_code=403)
        return await call_next(request)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return JSONResponse({'error': {'message': '请检查输入参数', 'fields': [
            {'location': list(error['loc']), 'message': error['msg']} for error in exc.errors()
        ]}}, status_code=422)

    @app.exception_handler(TaskConflict)
    async def conflict(request, exc):
        return JSONResponse({'error': {'message': str(exc)}}, status_code=409)

    @app.exception_handler(NovelCrawlError)
    async def business_error(request, exc):
        code = 404 if isinstance(exc, (SourceNotFound, ChapterNotFound, MergeEditionNotFound)) else 422
        return JSONResponse({'error': {'message': str(exc)}}, status_code=code)

    @app.exception_handler(ValueError)
    async def value_error(request, exc):
        return JSONResponse({'error': {'message': str(exc)}}, status_code=422)

    @app.exception_handler(sqlite3.Error)
    async def database_error(request, exc):
        logging.getLogger(__name__).exception('API 数据库错误')
        return JSONResponse({'error': {'message': '数据库操作失败，请查看本地日志'}}, status_code=500)

    async def read(method, *args, **kwargs):
        return await asyncio.to_thread(read_database, config.database_path, method, *args, **kwargs)

    @app.get('/api/app')
    async def app_info():
        manager = app.state.manager
        return {'name': 'NovelCrawl', 'version': '0.3.0', 'concurrency': {'default': 3, 'min': 1, 'max': 5},
                'batch_wait_seconds': config.batch_wait_seconds,
                'sites': [{'key': site.key, 'domains': site.domains} for site in SITE_CONFIGS.values()],
                'stopping': manager.stopping, 'cleanup_failed': manager.cleanup_failed,
                'database_path': str(config.database_path)}

    @app.get('/api/sources')
    async def sources():
        return await read('list_source_summaries')

    @app.get('/api/sources/{source_id}')
    async def source_detail(source_id: int):
        return await read('source_detail', source_id)

    @app.get('/api/sources/{source_id}/chapters')
    async def chapters(source_id: int, page: int = Query(1, ge=1),
                       page_size: int = Query(50, ge=1, le=200),
                       status: Literal['pending', 'completed', 'failed'] | None = None):
        return await read('chapter_page', source_id, page, page_size, status)

    @app.get('/api/chapters/{chapter_id}')
    async def chapter(chapter_id: int):
        return await read('chapter_preview', chapter_id)

    @app.get('/api/editions')
    async def editions():
        return [asdict(edition) for edition in await read('list_merged_editions')]

    @app.get('/api/editions/{edition_id}/chapters')
    async def edition_chapters(edition_id: int, page: int = Query(1, ge=1),
                               page_size: int = Query(50, ge=1, le=200)):
        return await read('edition_page', edition_id, page, page_size)

    @app.get('/api/editions/{edition_id}')
    async def edition_detail(edition_id: int):
        return asdict(await read('get_merged_edition', edition_id))

    @app.post('/api/tasks', status_code=202)
    async def submit_task(request: TaskRequest):
        return await app.state.manager.submit(request)

    @app.get('/api/tasks')
    async def tasks():
        return app.state.manager.list_tasks()

    @app.post('/api/tasks/stop-all', status_code=202)
    async def stop_all():
        app.state.manager.request_stop_all()
        return {'stopping': app.state.manager.stopping}

    @app.get('/api/tasks/{task_id}')
    async def task(task_id: str):
        try:
            return app.state.manager.snapshot(task_id)
        except KeyError:
            raise HTTPException(404, '任务不存在或历史已清理')

    @app.get('/api/exports/{export_type}/{object_id}/file')
    async def export_file(export_type: Literal['source', 'merged'], object_id: int):
        obj = await read('get_source' if export_type == 'source' else 'get_merged_edition', object_id)
        root = config.source_export_dir if export_type == 'source' else config.merged_export_dir
        path = (root / (obj.output_name+'.txt')).resolve()
        if path.parent != root.resolve() or not path.is_file():
            raise HTTPException(404, '尚未生成导出文件')
        return FileResponse(path, filename=path.name, media_type='text/plain; charset=utf-8')

    root = static_directory()
    if (root / 'index.html').is_file():
        app.mount('/', StaticFiles(directory=root, html=True), name='frontend')
    else:
        @app.get('/', response_class=HTMLResponse, status_code=503)
        async def missing_frontend():
            return '<h1>前端尚未构建</h1><p>请在 frontend 目录执行 npm.cmd ci 和 npm.cmd run build。</p>'
    return app
