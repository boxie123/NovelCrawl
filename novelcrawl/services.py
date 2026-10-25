"""下载/刷新业务：主协程收集完整结果，串行提交；导出由任务执行器负责。"""

import asyncio
import logging
import re
from contextlib import asynccontextmanager
from dataclasses import asdict
from urllib.parse import urlparse

from novelcrawl.browser_session import BrowserSession
from novelcrawl.control import RunControl, RunStopped
from novelcrawl.crawler import NovelCrawler
from novelcrawl.errors import SiteConfigNotFound
from novelcrawl.models import FetchResult
from novelcrawl.site_config import get_site_config, match_site_config
from novelcrawl.urls import normalize_url

logger = logging.getLogger(__name__)
BLOCKED_RESOURCE_TYPES = {'image', 'font', 'media', 'stylesheet'}
HEADLESS_CHROME_PATTERN = re.compile(r'\bHeadlessChrome/(?P<version>[0-9.]+)')


def normalize_browser_user_agent(user_agent):
    """保留实际 Chromium 版本，仅移除 Headless 产品标记。"""
    return HEADLESS_CHROME_PATTERN.sub(lambda m: 'Chrome/'+m.group('version'), user_agent, count=1)


async def block_unneeded_resources(route):
    if route.request.resource_type in BLOCKED_RESOURCE_TYPES:
        await route.abort()
    else:
        await route.continue_()


class DownloadService:
    def __init__(self, storage, config, *, control=None, report=None):
        self.storage = storage
        self.config = config
        self.control = control or RunControl()
        self.report = report or (lambda **fields: None)
        self.session = None
        self.active = set()
        self.successes = 0
        self.failures = 0
        self.total = 0
        self.error_summary = ''

    def request_stop(self):
        self.control.stop()
        if self.session is not None:
            self.session.request_stop()

    @asynccontextmanager
    async def _crawler_session(self, site_config, *, catalogue_url):
        self.session = BrowserSession(self.control)
        try:
            self.report(phase='starting')
            context = await self.session.start(self.config, normalize_browser_user_agent, block_unneeded_resources)
            yield NovelCrawler(context, site_config, catalogue_url=catalogue_url,
                               navigation_retries=self.config.navigation_retries, control=self.control)
        finally:
            await self.session.close()

    async def download_url(self, catalogue_url, *, start_position=None):
        """同步目录并下载 pending/failed；起始位置贯穿统计和队列。"""
        url = normalize_url(catalogue_url)
        site = match_site_config(url)
        if site is None:
            raise SiteConfigNotFound('没有匹配到站点配置: '+url)
        async with self._crawler_session(site, catalogue_url=url) as crawler:
            self.control.check()
            self.report(phase='catalogue')
            title, catalogue = await crawler.fetch_catalogue(url)
            self.control.check()
            hostname = urlparse(url).hostname or 'unknown'
            site_name = hostname.split('.')[-2] if '.' in hostname else hostname
            source_id = await self.storage.call('upsert_source', catalogue_url=url, title=title,
                site_name=site_name, site_config_key=site.key, output_name=f'{title}_{site_name}')
            self.control.check()
            await self.storage.call('sync_catalogue', source_id, catalogue)
            self.report(source_id=source_id)
            queue = await self.storage.call('get_download_queue', source_id, start_position=start_position)
            await self._run_chapters(crawler, queue, source_id=source_id,
                preserve_completed=False, progress_start_position=start_position)
        return source_id

    async def resume_source(self, source_id, *, start_position=None):
        source = await self.storage.call('get_source', source_id)
        result = await self.download_url(source.catalogue_url, start_position=start_position)
        if result != source_id:
            raise RuntimeError('续传时来源身份发生变化')
        return source_id

    async def refresh_source(self, source_id, *, chapter_ids=None):
        """按提交时固化的章节 ID 刷新；失败或停止保留已有正文。"""
        source = await self.storage.call('get_source', source_id)
        site = get_site_config(source.site_config_key)
        if site is None:
            raise SiteConfigNotFound('来源站点配置不存在')
        if chapter_ids is None:
            chapter_ids = await self.storage.call('chapter_ids', source_id)
        chapters = await self.storage.call('chapters_by_ids', chapter_ids, source_id)
        async with self._crawler_session(site, catalogue_url=source.catalogue_url) as crawler:
            self.control.check()
            self.report(phase='warmup')
            await crawler.warm_up(source.catalogue_url)
            self.control.check()
            await self._run_chapters(crawler, chapters, source_id=source_id,
                preserve_completed=True, progress_start_position=None)
        return source_id

    async def _run_chapters(self, crawler, chapters, *, source_id, preserve_completed, progress_start_position):
        self.total = len(chapters)
        self.report(total=self.total, processed=0, succeeded=0, failed=0)
        await self._progress(source_id, progress_start_position)
        for index in range(0, len(chapters), self.config.batch_size):
            self.control.check()
            self.report(phase='fetching', wait_until=None)
            await self._run_batch(crawler, chapters[index:index+self.config.batch_size],
                                  preserve_completed=preserve_completed)
            self.control.check()
            await self._progress(source_id, progress_start_position)
            if index+self.config.batch_size < len(chapters):
                import time
                self.report(phase='waiting', wait_until=time.time()+self.config.batch_wait_seconds)
                await self.control.wait(self.config.batch_wait_seconds)

    async def _progress(self, source_id, start_position):
        progress = await self.storage.call('get_progress', source_id, start_position=start_position)
        self.report(source_progress=asdict(progress))

    async def _run_batch(self, crawler, chapters, *, preserve_completed):
        unresolved = {c.id: c for c in chapters}
        attempts = {c.id: 0 for c in chapters}
        last_failures = {}
        for round_index in range(self.config.max_attempts_per_run):
            self.control.check()
            if not unresolved:
                break
            self.report(attempt=round_index+1, current_chapters=[c.position for c in unresolved.values()])
            pending = {asyncio.create_task(self._safe_fetch(crawler, c)) for c in unresolved.values()}
            self.active.update(pending)
            try:
                while pending:
                    done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                    for task in done:
                        self.active.discard(task)
                        result = task.result()
                        if result is None:
                            continue
                        attempts[result.chapter_id] += 1
                        if result.succeeded:
                            await self.storage.call('save_completed_chapter', result.chapter_id,
                                title=result.title, content=result.content,
                                attempts=attempts[result.chapter_id])
                            unresolved.pop(result.chapter_id, None)
                            last_failures.pop(result.chapter_id, None)
                            self.successes += 1
                            self._report_counts()
                        elif not self.control.stopped.is_set():
                            last_failures[result.chapter_id] = result
            except BaseException:
                # 主编排出错先关闭资源，再收集子任务异常，不能留下背景请求。
                self.request_stop()
                if self.session:
                    await self.session.close()
                if pending:
                    await asyncio.gather(*pending, return_exceptions=True)
                self.active.difference_update(pending)
                raise
            self.control.check()
        for chapter_id, result in last_failures.items():
            self.control.check()
            await self.storage.call('save_failed_chapter', chapter_id,
                error_type=result.error_type or 'unknown', error_message=result.error_message or '未知错误',
                attempts=attempts[chapter_id], preserve_completed=preserve_completed)
            self.failures += 1
            message = f'章节 {chapter_id}：{result.error_message or "未知错误"}'
            logger.warning('章节最终失败：%s', message)
            self.error_summary = (self.error_summary + message + '\n').encode('utf-8')[:4096].decode('utf-8', errors='ignore')
            self.report(error=self.error_summary)
            self._report_counts()

    def _report_counts(self):
        self.report(processed=self.successes+self.failures, succeeded=self.successes, failed=self.failures)

    async def _safe_fetch(self, crawler, chapter):
        """单章普通异常转成结果；停止异常不写成抓取失败。"""
        try:
            self.control.check()
            return await crawler.fetch_chapter(chapter)
        except RunStopped:
            return None
        except Exception as exc:
            if self.control.stopped.is_set():
                return None
            logger.exception('章节抓取异常：chapter_id=%s', chapter.id)
            return FetchResult(chapter.id, chapter.normalized_title or chapter.catalogue_title or '', None,
                'unknown', f'{exc}；详细堆栈见 logs/novelcrawl.log')
