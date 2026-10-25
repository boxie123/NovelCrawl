"""生产 GUI 多页目录验收，全部数据与 HTTP 均位于本机临时环境。"""
import asyncio
import socket
import tempfile
import unittest
from pathlib import Path

import uvicorn
from playwright.async_api import async_playwright, expect
from novelcrawl.gui import GuiServer
from novelcrawl.models import CatalogueChapter
from novelcrawl.storage import Storage
from novelcrawl.web import create_app
from tests.test_gui_backend import configuration, populate


class PaginationGuiTests(unittest.IsolatedAsyncioTestCase):
    async def test_large_directory_selection_preview_and_edition_paging(self):
        with tempfile.TemporaryDirectory() as directory:
            config=configuration(Path(directory));source,_=populate(config)
            with Storage(config.database_path) as storage:
                storage.sync_catalogue(source,[CatalogueChapter(f'https://www.sudugu.org/chapter/{i}',f'第{i}章',f'第{i}章',i) for i in range(1,126)])
                ids=storage.chapter_ids(source)
                for chapter_id in (ids[0],ids[49],ids[50],ids[-1]):
                    storage.save_completed_chapter(chapter_id,title=f'第{ids.index(chapter_id)+1}章',content='仅用于本机验收的模拟正文。')
                edition=storage.create_merged_edition('多页合并版本')
                storage.merge_from_ids(edition,ids)
            app=create_app(config)
            sock=socket.socket();sock.bind(('127.0.0.1',0));sock.setblocking(False)
            base=f'http://127.0.0.1:{sock.getsockname()[1]}'
            server=GuiServer(uvicorn.Config(app,log_level='error'),app)
            serving=asyncio.create_task(server.serve(sockets=[sock]))
            playwright=await async_playwright().start();browser=None
            try:
                while not server.started:
                    if serving.done(): await serving
                    await asyncio.sleep(.01)
                browser=await playwright.chromium.launch()
                page=await browser.new_page(viewport={'width':1440,'height':1000})
                errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
                await page.goto(base+f'/#/sources/{source}')
                bottom=page.get_by_role('group',name='目录底部翻页',exact=True)
                await expect(bottom).to_contain_text('共 125 章')
                await page.get_by_label('选择第 1 章',exact=True).check()
                await bottom.get_by_role('button',name='下一页',exact=True).click()
                await expect(page.get_by_label('选择第 51 章',exact=True)).to_be_visible()
                await page.get_by_label('选择第 51 章',exact=True).check()
                await expect(page.locator('.panel-heading')).to_contain_text('已选 2 章')
                await page.get_by_role('button',name='预览 →',exact=True).first.click()
                dialog=page.get_by_role('dialog')
                await expect(dialog.get_by_role('heading')).to_have_text('第51章')
                await dialog.get_by_role('button',name='← 上一章',exact=True).click()
                await expect(dialog.get_by_role('heading')).to_have_text('第50章')
                await dialog.get_by_role('button',name='下一章 →',exact=True).click()
                await expect(dialog.get_by_role('heading')).to_have_text('第51章')
                await dialog.get_by_role('button',name='关闭正文预览').click()
                await bottom.get_by_label('页码',exact=True).fill('99')
                await bottom.get_by_label('页码',exact=True).press('Enter')
                await expect(bottom.get_by_role('alert')).to_contain_text('1～3')
                await bottom.get_by_role('button',name='末页',exact=True).click()
                await expect(page.get_by_label('选择第 125 章',exact=True)).to_be_visible()
                await expect(bottom.get_by_role('button',name='下一页',exact=True)).to_be_disabled()
                await bottom.get_by_label('每页章节数',exact=True).select_option('100')
                await expect(bottom.get_by_label('页码',exact=True)).to_have_value('2')
                await expect(page.get_by_label('选择第 101 章',exact=True)).to_be_visible()
                await page.get_by_label('按章节状态筛选').select_option('completed')
                await expect(bottom.get_by_label('页码',exact=True)).to_have_value('1')
                await expect(bottom).to_contain_text('共 4 章')
                await expect(page.locator('.panel-heading')).to_contain_text('已选 0 章')
                await page.get_by_label('按章节状态筛选').select_option('pending')
                await expect(bottom).to_contain_text('共 121 章')
                await bottom.get_by_role('button',name='末页',exact=True).click()
                await expect(bottom.get_by_label('页码',exact=True)).to_have_value('2')
                # 当前筛选的末页被后台完成更新清空后，自动回到有效第一页。
                for chapter_id in ids[90:]:
                    await app.state.manager.gateway.call('save_completed_chapter',chapter_id,title='已完成',content='模拟正文')
                await expect(bottom.get_by_label('页码',exact=True)).to_have_value('1',timeout=12000)
                self.assertGreater(await page.locator('tbody tr').count(),0)
                await page.goto(base+f'/#/editions/{edition}')
                merged=page.get_by_role('group',name='合并目录翻页',exact=True)
                await expect(merged).to_contain_text('共 125 章')
                await merged.get_by_role('button',name='末页',exact=True).click()
                await expect(merged.get_by_label('页码',exact=True)).to_have_value('3')
                await page.get_by_label('从来源选择',exact=True).select_option(str(source))
                picker=page.get_by_role('group',name='来源选章翻页',exact=True)
                await picker.get_by_role('button',name='下一页',exact=True).click()
                await expect(page.get_by_role('button',name='添加第 51 章到组合',exact=True)).to_be_visible()
                await page.get_by_role('button',name='添加第 51 章到组合',exact=True).click()
                await expect(page.get_by_role('button',name='组合 1 个部分并追加',exact=True)).to_be_enabled()
                shots=Path('.cache/acceptance');shots.mkdir(parents=True,exist_ok=True)
                await page.screenshot(path=str(shots/'pagination-desktop.png'),full_page=True)
                await page.set_viewport_size({'width':390,'height':844})
                await page.screenshot(path=str(shots/'pagination-mobile.png'),full_page=True)
                self.assertTrue(await page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
                self.assertEqual(errors,[])
            finally:
                if browser: await browser.close()
                await playwright.stop()
                server.should_exit=True;await serving;sock.close()
