"""跨协程/工作线程共享的停止令牌；不强制取消 Playwright 调用。"""

import asyncio
import threading


class RunStopped(Exception):
    """用户主动停止，不作为章节抓取失败。"""


class CleanupError(RuntimeError):
    """资源回收未确认，禁止调度后续任务。"""


class RunControl:
    def __init__(self):
        self.stopped = threading.Event()
        self.wakeup = asyncio.Event()
        self._gate = threading.Lock()
        self.commit_reserved = False

    def stop(self):
        """只在事件循环线程调用；锁内只变更标记，不执行 I/O。"""
        with self._gate:
            self.stopped.set()
        self.wakeup.set()

    def check(self):
        if self.stopped.is_set():
            raise RunStopped("已主动停止")

    def reserve_commit(self):
        """原子裁决停止与最终提交；取得提交权后不再撤销已完成结果。"""
        with self._gate:
            self.check()
            self.commit_reserved = True

    async def wait(self, seconds):
        self.check()
        try:
            await asyncio.wait_for(self.wakeup.wait(), seconds)
        except asyncio.TimeoutError:
            pass
        self.check()
