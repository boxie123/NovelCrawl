"""拥有一次抓取的浏览器；停止通过关闭资源中断请求，不 cancel Playwright。"""

import asyncio

from playwright.async_api import async_playwright
from playwright_stealth import Stealth

from novelcrawl.control import CleanupError


class BrowserSession:
    def __init__(self, control):
        self.control = control
        self.playwright = None
        self.browser = None
        self.close_task = None
        self.driver_task = None
        self.context = None

    def request_stop(self):
        """立即开始关闭独占 Browser；初始化迟到资源由 start/finally 接管。"""
        if self.browser is not None and self.close_task is None:
            self.close_task = asyncio.create_task(self.browser.close(reason='用户停止任务'))
        elif self.browser is None and self.playwright is not None and self.driver_task is None:
            self.driver_task = asyncio.create_task(self.playwright.stop())

    async def start(self, config, normalize_ua, route_handler):
        self.control.check()
        self.playwright = await async_playwright().start()
        self.control.check()
        self.browser = await self.playwright.chromium.launch(timeout=30_000)
        self.control.check()
        probe = await self.browser.new_context()
        self.control.check()
        page = await probe.new_page()
        self.control.check()
        ua = await page.evaluate('navigator.userAgent')
        self.control.check()
        await probe.close()
        self.control.check()
        self.context = await self.browser.new_context(
            java_script_enabled=True, user_agent=normalize_ua(ua)
        )
        self.control.check()
        await Stealth(init_scripts_only=True).apply_stealth_async(self.context)
        self.control.check()
        await self.context.route('**/*', route_handler)
        self.control.check()
        self.context.set_default_timeout(config.navigation_timeout_ms)
        return self.context

    async def close(self):
        """有界观察关闭调用；超时升级到驱动关闭，不取消正在关闭的调用。"""
        errors = []
        if self.browser is not None and self.close_task is None:
            self.close_task = asyncio.create_task(self.browser.close())
        if self.close_task is not None:
            done, _ = await asyncio.wait({self.close_task}, timeout=5)
            if done:
                try:
                    self.close_task.result()
                except Exception as exc:
                    errors.append(exc)
        if self.playwright is not None and self.driver_task is None:
            self.driver_task = asyncio.create_task(self.playwright.stop())
        pending = {t for t in (self.close_task, self.driver_task) if t is not None}
        if pending:
            done, pending = await asyncio.wait(pending, timeout=5)
            for task in done:
                try:
                    task.result()
                except Exception as exc:
                    errors.append(exc)
            if pending:
                # 保留强引用，任务管理器会阻断后续抓取并报告清理异常。
                raise CleanupError('浏览器资源清理未完成，请退出服务后重试')
        if self.browser is not None and self.browser.is_connected():
            raise CleanupError('浏览器仍连接，未确认停止')
        if errors:
            raise CleanupError(str(errors[-1]))
