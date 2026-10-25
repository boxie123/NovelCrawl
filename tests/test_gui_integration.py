"""本地 Chromium + HTTP 验收；仅访问 127.0.0.1，不使用真实项目数据。"""

import asyncio
import os
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import uvicorn
from playwright.async_api import async_playwright

from novelcrawl.gui import GuiServer, loop_factory
from novelcrawl.models import SiteConfig
from novelcrawl.site_config import SITE_CONFIGS
from novelcrawl.task_manager import TaskManager
from novelcrawl.web import create_app, static_directory
from tests.test_gui_backend import configuration, populate, request
from tests.test_download_service import SessionlessDownloadService


class LocalBrowserTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.config = configuration(Path(self.temp.name))
        self.site_tasks = set()
        self.release = asyncio.Event()
        self.hanging = asyncio.Event()
        self.visits = []

        async def handle(reader, writer):
            task = asyncio.current_task(); self.site_tasks.add(task)
            try:
                head = await reader.readuntil(b'\r\n\r\n')
                path = head.split(b' ')[1].decode()
                self.visits.append(path)
                if path == '/book/':
                    html = '<h1>本地测试作品</h1><ul><li><a href="/chapter/1">第一章</a></li><li><a href="/chapter/2">第二章</a></li></ul>'
                elif path == '/chapter/2':
                    self.hanging.set()
                    # 客户端停止时连接应主动断开；也允许测试收尾释放。
                    eof = asyncio.create_task(reader.read())
                    release = asyncio.create_task(self.release.wait())
                    done, pending = await asyncio.wait({eof, release}, return_when=asyncio.FIRST_COMPLETED)
                    for pending_task in pending:
                        pending_task.cancel()
                    await asyncio.gather(*pending, return_exceptions=True)
                    html = '<div class="body">'+('第二章测试文字。'*100)+'</div>'
                else:
                    html = '<div class="body">'+('第一章测试文字。'*100)+'</div>'
                body = html.encode('utf-8')
                writer.write(b'HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nConnection: close\r\nContent-Length: '+str(len(body)).encode()+b'\r\n\r\n'+body)
                await writer.drain()
            except (ConnectionError, asyncio.IncompleteReadError):
                pass
            finally:
                writer.close()
                try:
                    await writer.wait_closed()
                except ConnectionError:
                    pass
                self.site_tasks.discard(task)

        self.site = await asyncio.start_server(handle, '127.0.0.1', 0)
        self.port = self.site.sockets[0].getsockname()[1]
        self.url = f'http://127.0.0.1:{self.port}/book/'
        self.site_config = SiteConfig('local-fixture', ('127.0.0.1',), 'ul li', '', 'h1', '.body', '')
        self.patch = patch.dict(SITE_CONFIGS, {'local-fixture':self.site_config})
        self.patch.start()
        self.manager = TaskManager(self.config)
        await self.manager.start()

    async def asyncTearDown(self):
        self.release.set()
        await self.manager.close()
        self.site.close(); await self.site.wait_closed()
        if self.site_tasks:
            await asyncio.gather(*self.site_tasks, return_exceptions=True)
        self.patch.stop()
        self.temp.cleanup()

    async def test_hanging_navigation_stops_without_waiting_for_response(self):
        if sys.platform == 'win32':
            self.assertIsInstance(asyncio.get_running_loop(), asyncio.ProactorEventLoop)
        task = await self.manager.submit(request('download',url=self.url,concurrency=2))
        await asyncio.wait_for(self.hanging.wait(),30)
        for _ in range(300):
            if self.manager.snapshot(task['id'])['succeeded'] == 1:
                break
            await asyncio.sleep(.01)
        self.assertEqual(self.manager.snapshot(task['id'])['succeeded'],1)
        session = self.manager.service.session
        browser = session.browser
        self.manager.request_stop_all()
        self.manager.request_stop_all()
        await asyncio.wait_for(self.manager.queue.join(),10)
        self.assertFalse(browser.is_connected())
        self.assertTrue(session.driver_task.done())
        self.assertFalse(self.manager.service)
        result = self.manager.snapshot(task['id'])
        self.assertEqual(result['status'],'cancelled',result)
        source = result['source_id']
        progress = await self.manager.gateway.call('get_progress',source)
        self.assertEqual((progress.completed,progress.pending,progress.failed),(1,1,0))
        self.assertEqual(self.visits.count('/chapter/2'),1)
        self.assertFalse(self.config.source_export_dir.exists())


class BuiltGuiTests(unittest.IsolatedAsyncioTestCase):
    async def test_built_ui_create_preview_merge_export_and_reopen(self):
        self.assertTrue((static_directory()/'index.html').is_file(), '先构建前端再运行集成验收')
        with tempfile.TemporaryDirectory() as directory:
            config = configuration(Path(directory))
            source, ids = populate(config)
            entered, release = asyncio.Event(), asyncio.Event()
            class PausedService(SessionlessDownloadService):
                async def download_url(inner, *args, **kwargs):
                    entered.set()
                    await release.wait()
                    return await super().download_url(*args, **kwargs)
            app = create_app(config, manager_factory=lambda cfg: TaskManager(cfg,service_factory=PausedService))
            sock = socket.socket(); sock.bind(('127.0.0.1',0)); sock.setblocking(False)
            port = sock.getsockname()[1]
            server = GuiServer(uvicorn.Config(app,log_level='error',timeout_graceful_shutdown=1),app)
            serving = asyncio.create_task(server.serve(sockets=[sock]))
            playwright = await async_playwright().start()
            browser = None
            try:
                while not server.started:
                    if serving.done():
                        await serving
                    await asyncio.sleep(.01)
                browser = await playwright.chromium.launch()
                page = await browser.new_page(viewport={'width':1440,'height':1000})
                screenshots=Path('.cache')/'acceptance'; screenshots.mkdir(parents=True,exist_ok=True)
                errors = []
                page.on('pageerror',lambda error: errors.append(str(error)))
                await page.goto(f'http://127.0.0.1:{port}')
                await page.get_by_role('link',name='管理章节 →').click()
                await page.get_by_role('button',name='同步目录并续传').click()
                await asyncio.wait_for(entered.wait(), 5)
                await page.close()
                self.assertIsNotNone(app.state.manager.active)
                release.set()
                await asyncio.wait_for(app.state.manager.queue.join(),10)
                self.assertEqual((await app.state.manager.gateway.call('get_progress',source)).completed,3)
                page = await browser.new_page(viewport={'width':1440,'height':1000})
                page.on('pageerror',lambda error: errors.append(str(error)))
                await page.goto(f'http://127.0.0.1:{port}/#/sources/{source}')
                await page.get_by_role('button',name='预览 →').first.click()
                await page.get_by_role('dialog').wait_for()
                self.assertIn('第1章正文',await page.get_by_role('dialog').inner_text())
                await page.screenshot(path=str(screenshots/'preview.png'),full_page=True)
                await page.get_by_role('button',name='关闭正文预览').click()
                await page.screenshot(path=str(screenshots/'source.png'),full_page=True)
                await page.get_by_role('link',name='合并版本').click()
                await page.get_by_role('button',name='＋ 创建版本').click()
                await page.get_by_label('版本名称',exact=True).fill('验收合并版')
                await page.get_by_label('版本说明（可选）').fill('临时测试内容')
                await page.get_by_label('输出文件名（可选）').fill('验收输出')
                await page.get_by_role('button',name='创建版本',exact=True).click()
                await page.get_by_role('link',name='验收合并版').click()
                await page.get_by_label('选择来源',exact=True).select_option(str(source))
                await page.get_by_label('连续目录范围').fill('1-2')
                await page.get_by_role('button',name='追加到版本末尾').click()
                await page.get_by_label('从来源选择').select_option(str(source))
                await page.get_by_role('button',name='添加第 3 章到组合').click()
                await page.get_by_label('合并章节标题（可选）').fill('第三章组合')
                await page.get_by_role('button',name='组合 1 个部分并追加').click()
                await app.state.manager.queue.join()
                # 等待提交完成提示，避免 export 与仍在 HTTP 提交的组合操作竞争。
                await page.get_by_text('第三章组合',exact=True).wait_for()
                await page.screenshot(path=str(screenshots/'edition.png'),full_page=True)
                await page.get_by_role('button',name='导出合并 TXT').click()
                await page.get_by_role('link',name='任务中心').click()
                async with page.expect_download() as download:
                    await page.get_by_role('link',name='下载 TXT ↓').first.click()
                self.assertTrue((await download.value).suggested_filename.endswith('.txt'))
                await page.screenshot(path=str(screenshots/'tasks.png'),full_page=True)
                editions = await app.state.manager.gateway.call('list_merged_editions')
                detail = await app.state.manager.gateway.call('edition_page',editions[0].id)
                self.assertEqual(detail['total'],3)
                self.assertEqual(detail['items'][2]['parts'][0]['id'],ids[2])
                self.assertEqual(errors,[])
                screenshots=Path('.cache')/'acceptance'; screenshots.mkdir(parents=True,exist_ok=True)
                await page.goto(f'http://127.0.0.1:{port}/#/')
                await page.get_by_role('link',name='管理章节 →').wait_for()
                await page.screenshot(path=str(screenshots/'library.png'),full_page=True)
                await page.set_viewport_size({'width':390,'height':844})
                await page.screenshot(path=str(screenshots/'mobile.png'),full_page=True)
                self.assertTrue(await page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'))
            finally:
                release.set()
                if browser is not None:
                    await browser.close()
                await playwright.stop()
                server.should_exit=True
                await serving
                sock.close()

class ExitSignalTests(unittest.TestCase):
    def test_first_sigint_stops_browser_before_slow_download_finishes(self):
        import subprocess
        completed = subprocess.run([sys.executable, '-m', 'tests.gui_exit_probe'],
            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=45,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn('"driver_exited": true', completed.stdout, completed.stdout + completed.stderr)
        self.assertIn('"slow_response_interrupted": true', completed.stdout)

class LauncherTests(unittest.TestCase):
    def test_occupied_port_fails_before_app_initialization(self):
        import contextlib
        import io
        from novelcrawl.gui import main
        with patch('novelcrawl.gui.socket.socket') as socket_factory, patch('novelcrawl.gui.create_app') as create:
            socket_factory.return_value.bind.side_effect=OSError('address in use')
            output=io.StringIO()
            with contextlib.redirect_stderr(output):
                self.assertEqual(main(),1)
            self.assertIn('8765',output.getvalue())
            create.assert_not_called()
            socket_factory.return_value.close.assert_called_once()

    def test_runner_factory_supports_subprocesses_on_windows(self):
        loop=loop_factory()
        try:
            if sys.platform=='win32':
                self.assertIsInstance(loop,asyncio.ProactorEventLoop)
            self.assertIs(asyncio.get_event_loop(),loop)
        finally:
            loop.close(); asyncio.set_event_loop(None)
