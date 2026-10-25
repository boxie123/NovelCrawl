"""在专用线程拥有 SQLite 写连接，主协程逐次等待完整操作。"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from functools import partial

from novelcrawl.storage import Storage


class StorageGateway:
    def __init__(self, path):
        self.path = path
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="novel-db")
        self.storage = None
        self.closed = False

    async def _execute(self, function, *args, **kwargs):
        future = asyncio.get_running_loop().run_in_executor(
            self.executor, partial(function, *args, **kwargs)
        )
        try:
            return await asyncio.shield(future)
        except asyncio.CancelledError:
            # 取消 await 不能取消 SQLite 事务；确认执行结果后才能关闭连接。
            await future
            raise

    async def open(self):
        def initialize():
            self.storage = Storage(self.path)
            self.storage.__enter__()
        await self._execute(initialize)

    async def call(self, method, *args, **kwargs):
        return await self._execute(lambda: getattr(self.storage, method)(*args, **kwargs))

    async def close(self):
        if self.closed:
            return
        try:
            if self.storage is not None:
                await self._execute(self.storage.close)
        finally:
            await asyncio.to_thread(self.executor.shutdown, wait=True)
            self.closed = True


def read_database(path, method, *args, **kwargs):
    """连接不穿越线程/请求边界；一项查询使用一个短快照。"""
    with Storage(path, read_only=True) as storage:
        storage.connection.execute("BEGIN")
        return getattr(storage, method)(*args, **kwargs)
