"""本机 GUI 启动器：显式 Windows 子进程事件循环与服务退出。"""

import asyncio
import os
import socket
import sys
import webbrowser

import uvicorn

from novelcrawl.config import DEFAULT_CONFIG
from novelcrawl.logging_config import configure_logging
from novelcrawl.web import create_app, static_directory


class GuiServer(uvicorn.Server):
    def __init__(self, config, app):
        super().__init__(config)
        self.app = app

    async def shutdown(self, sockets=None):
        # 先停止抓取，再等待 HTTP 文件响应；lifespan 负责最终回收。
        manager = getattr(self.app.state, 'manager', None)
        if manager is not None:
            manager.request_stop_all()
        await super().shutdown(sockets=sockets)


def loop_factory():
    loop = asyncio.ProactorEventLoop() if sys.platform == 'win32' else asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    return loop


async def serve(app, sock, *, open_browser=True):
    """使用现有循环启动；ready 之前不打开默认浏览器。"""
    server = GuiServer(uvicorn.Config(app, host='127.0.0.1', port=sock.getsockname()[1],
                                     workers=1, reload=False, timeout_graceful_shutdown=5,
                                     log_level='warning'), app)
    async def open_when_ready():
        while not server.started and not server.should_exit:
            await asyncio.sleep(0.05)
        if server.started and open_browser:
            await asyncio.to_thread(webbrowser.open, f'http://127.0.0.1:{sock.getsockname()[1]}')
    opener = asyncio.create_task(open_when_ready())
    try:
        await server.serve(sockets=[sock])
    finally:
        opener.cancel()
        await asyncio.gather(opener, return_exceptions=True)
    return server.started


def main():
    if not (static_directory() / 'index.html').is_file():
        print('请先在 frontend 目录执行 npm.cmd ci 和 npm.cmd run build。', file=sys.stderr)
        return 1
    local_browsers = DEFAULT_CONFIG.database_path.parent.parent / '.cache' / 'browsers'
    if local_browsers.is_dir() and 'PLAYWRIGHT_BROWSERS_PATH' not in os.environ:
        os.environ['PLAYWRIGHT_BROWSERS_PATH'] = str(local_browsers)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if sys.platform == 'win32':
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        sock.bind(('127.0.0.1', 8765))
        sock.setblocking(False)
        configure_logging(DEFAULT_CONFIG.log_path)
        app = create_app()
        print('NovelCrawl：http://127.0.0.1:8765（关闭网页继续运行，Ctrl+C 退出服务）')
        with asyncio.Runner(loop_factory=loop_factory) as runner:
            started = runner.run(serve(app, sock))
        return 0 if started else 1
    except KeyboardInterrupt:
        return 0
    except OSError as exc:
        print(f'启动失败：端口 8765 可能已被占用。{exc}', file=sys.stderr)
        return 1
    finally:
        sock.close()
