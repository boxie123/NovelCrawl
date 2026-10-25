"""真实 SIGINT 与慢 TXT 响应退出探针，仅子进程执行。"""
import asyncio
import json
import signal
import socket
import tempfile
from pathlib import Path

import uvicorn
from fastapi.responses import FileResponse
from novelcrawl.gui import GuiServer, loop_factory
from novelcrawl.web import create_app
from tests.test_gui_integration import LocalBrowserTests


async def probe():
    fixture = LocalBrowserTests()
    await fixture.asyncSetUp()
    socket_server = socket.socket()
    socket_server.bind(('127.0.0.1', 0)); socket_server.setblocking(False)
    app = create_app(fixture.config, manager_factory=lambda _: fixture.manager)
    # fixture 已启动任务管理器，lifespan 只接管退出。
    async def started():
        pass
    fixture.manager.start = started
    path = Path(fixture.temp.name) / 'slow.txt'
    with path.open('wb') as file:
        block = b'local test\n' * 100000
        for _ in range(64):
            file.write(block)
    response_started = asyncio.Event()
    # 放在静态 mount 之前，HTTP 仍使用真实 FileResponse。
    async def slow_file(request):
        response_started.set()
        return FileResponse(path)
    from starlette.routing import Route
    app.router.routes.insert(0, Route('/slow.txt', slow_file))
    server = GuiServer(uvicorn.Config(app, log_level='critical', timeout_graceful_shutdown=1), app)
    from tests.test_gui_backend import request
    task = await fixture.manager.submit(request('download', url=fixture.url, concurrency=2))
    serving = asyncio.create_task(server.serve(sockets=[socket_server]))
    writer = None
    try:
        await asyncio.wait_for(fixture.hanging.wait(), 30)
        while not server.started:
            await asyncio.sleep(.01)
        session = fixture.manager.service.session
        driver = session.playwright._impl_obj._connection._transport._proc
        reader, writer = await asyncio.open_connection('127.0.0.1', socket_server.getsockname()[1], limit=1024)
        writer.write(b'GET /slow.txt HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n')
        await writer.drain()
        await asyncio.wait_for(response_started.wait(), 5)
        await reader.readuntil(b'\r\n\r\n')
        await asyncio.sleep(.05)
        assert server.server_state.tasks, '响应应仍在发送'
        # 真实操作系统 SIGINT 进入 Uvicorn 捕获器，等价于首次 Ctrl+C。
        signal.raise_signal(signal.SIGINT)
        for _ in range(100):
            if not session.browser.is_connected():
                break
            await asyncio.sleep(.01)
        assert not session.browser.is_connected(), 'HTTP 收尾前应停止抓取'
        assert server.server_state.tasks, '抓取停止时慢响应仍未结束'
        await serving
    finally:
        if writer:
            writer.close()
            await writer.wait_closed()
        server.should_exit = True
        if not serving.done():
            await serving
        socket_server.close()
        result = fixture.manager.snapshot(task['id'])
        assert result['status'] == 'cancelled', result
        assert driver.returncode == 0, driver.returncode
        assert not fixture.config.source_export_dir.exists()
        await fixture.asyncTearDown()
        print(json.dumps({'cancelled':True, 'driver_exited':True, 'slow_response_interrupted':True}), flush=True)


if __name__ == '__main__':
    try:
        with asyncio.Runner(loop_factory=loop_factory) as runner:
            runner.run(probe())
    except KeyboardInterrupt:
        pass
