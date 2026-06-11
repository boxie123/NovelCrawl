import re
import asyncio
from math import ceil
from typing import List, Dict
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from playwright.async_api import BrowserContext, async_playwright
from playwright_stealth import stealth_async
from utils import chineseNumber2Int, remove_title, txt_write

import page_config

VOLUME_PATTERN = re.compile("正文卷")
CHAPTER_NUM_PATTERN = re.compile(r"^\s?第?([0-9一二三四五六七八九十百千万亿零壹贰叁肆伍陆柒捌玖拾佰仟]+)[章张]{0,1}")
SKIP_PATTERN = re.compile("(正在手打中，请稍等片刻，内容更新后，请重新刷新页面，即可获取最新更新！)|(网页版章节内容慢)")

web_name = ""


def get_catalogue_url_list(content_list):
    """从目录页 HTML 列表中提取章节标题和 URL。

    Args:
        content_list: 目录页 HTML 内容列表，每项为一个页面的 HTML 字符串。

    Returns:
        tuple[List[Dict], str]: 章节信息列表和小说名。每章包含 href（绝对 URL）和 title。
    """
    now_title = ""
    zhangjie_list = []
    for i in range(len(content_list)):
        content = content_list[i]
        catalogue_soup = BeautifulSoup(content, "lxml")
        # print(catalogue_soup)
        if i == 0:
            try:
                now_title = catalogue_soup.select(catalogue_title_selector)[0].string
            except AttributeError:
                now_title = input("获取小说名失败, 请手动输入:")
        zhangjie_list.extend(catalogue_soup.select(catalogue_selector))
    print(f"共获取到 {len(zhangjie_list)} 章小说链接")

    zhangjie_list_href = []
    for i in range(len(zhangjie_list)):
        relative_link = zhangjie_list[i].a["href"]
        zhangjie_title = zhangjie_list[i].a.string.strip()
        # relative_link = zhangjie_list[i].get("href")
        if not (relative_link or zhangjie_title):
            continue
        zhangjie_list_href.append({
            "href":urljoin(catalogue_url, relative_link),
            "title":zhangjie_title,
        })

    return zhangjie_list_href, now_title


def get_novel_content(novel_page_list, novel_title) -> bool:
    """解析章节正文并写入 TXT 文件。

    Args:
        novel_page_list: 页面内容列表，每项含 content（HTML）和 title（章节标题）。
        novel_title: 小说名，用于生成文件名。

    Returns:
        bool: True 表示成功写入，False 表示跳过（正文为空/选择器未匹配/风控拦截）。
    """
    novel_text = ""
    zhangjie_title = ""
    for i in range(len(novel_page_list)):
        novel_page = novel_page_list[i]["content"]
        novel_soup = BeautifulSoup(novel_page, "lxml")
        if i == 0:
            # zhangjie_title = str(novel_soup.select("div.con p")[0].string).strip()
            zhangjie_title = novel_page_list[i]["title"]
            if re.match(VOLUME_PATTERN, zhangjie_title):
                zhangjie_title = re.sub(r'^正文卷\s*', '', zhangjie_title)
            title_match = re.match(CHAPTER_NUM_PATTERN, zhangjie_title)
            if title_match:
                raw_num = title_match.group(1)
                if not raw_num.isdecimal():
                    raw_num = chineseNumber2Int(raw_num)
                zhangjie_num = f"第{str(raw_num)}章"
                zhangjie_title = zhangjie_title.replace(title_match.group(0), zhangjie_num)
        novel_content_tag_list = novel_soup.select(novel_selector)
        if novel_content_tag_list:
            novel_content_tag = novel_content_tag_list[0]
        else:
            print(f"{zhangjie_title} 不存在 {novel_selector}")
            return False

        for extract_element in novel_content_tag.find_all("div"):
            extract_element.extract()

        novel_text += novel_content_tag.get_text("\n\t", strip=True)

    if not novel_text:
        print(f"警告: {zhangjie_title} 正文为空")
        return False

    if len(novel_text) < 400 and re.search(SKIP_PATTERN, novel_text):
        print(f"已跳过: {zhangjie_title}")
        return False

    novel_text += "\n\n"
    txt_write(novel_title + web_name, zhangjie_title, novel_text)
    return True


async def page_goto(page, url, wait_until: str = "domcontentloaded") -> str:
    """导航至目标 URL 并返回页面 HTML，失败时自动重试 5 次。

    Args:
        page: Playwright Page 对象。
        url: 目标 URL。
        wait_until: Playwright 的 wait_until 策略，默认 "domcontentloaded"。

    Returns:
        str: 页面 HTML 内容。

    Raises:
        TimeoutError: 5 次重试均失败。
    """
    for _ in range(5):
        try:
            resp = await page.goto(url, wait_until=wait_until)
        except TimeoutError:
            continue
        if resp and resp.status == 200:
            break
        await asyncio.sleep(5.0)
    else:
        raise TimeoutError(f"{url}获取失败")
    content = await page.content()
    return content


async def get_novel_page_content(
        context: BrowserContext, novel_info: dict,
        wait_until: str = "domcontentloaded",
        content_list = None,
        page = None,
) -> List[Dict[str, str]]:
    """爬取单个章节的正文内容，支持翻页。

    首次调用时创建新 Page，递归翻页时复用同一个 Page。

    Args:
        context: Playwright BrowserContext。
        novel_info: 含 href（章节 URL）和 title（章节标题）的字典。
        wait_until: Page 加载策略。
        content_list: 递归传递的内容累积列表，首次调用勿传。
        page: 递归传递的 Page 对象，首次调用勿传。

    Returns:
        List[Dict]: 每项含 content（HTML）和 title，对应章节的各页内容。
    """
    if content_list is None:
        content_list = []
    if page is None:
        page = await context.new_page()
        await stealth_async(page)
    url = novel_info["href"]
    content = await page_goto(page, url, wait_until)
    content_list.append({
        "content": content,
        "title": novel_info.get("title")
    })

    if novel_btn_selector == "":
        await page.close()
        return content_list

    soup = BeautifulSoup(content, "lxml")
    novel_btn = soup.select(novel_btn_selector)
    if len(novel_btn) > 0:
        novel_next_btn = novel_btn[-1]
        # print(novel_next_btn, "\n", novel_next_btn.string)
        if novel_next_btn.get_text(strip=True) == "下一页":
            next_url = urljoin(url, novel_next_btn["href"])
            next_novel_info = {
                "href": next_url,
                "title": novel_info.get("title")
            }
            return await get_novel_page_content(context, next_novel_info, wait_until, content_list, page)
        else:
            await page.close()
            return content_list

    await page.close()
    return content_list


async def get_catalogue_page_content(
        context: BrowserContext, url: str,
        wait_until: str = "domcontentloaded",
        content_list = None,
        page = None,
) -> List[str]:
    """爬取目录页 HTML，支持翻页。

    首次调用时创建新 Page，递归翻页时复用同一个 Page。
    遇到 javascript:void(0); 时停止翻页。

    Args:
        context: Playwright BrowserContext。
        url: 目录页 URL。
        wait_until: Page 加载策略。
        content_list: 递归传递的内容累积列表，首次调用勿传。
        page: 递归传递的 Page 对象，首次调用勿传。

    Returns:
        List[str]: 各目录页的 HTML 字符串列表。
    """
    if content_list is None:
        content_list = []
    if page is None:
        page = await context.new_page()
        await stealth_async(page)

    content = await page_goto(page, url, wait_until)
    content_list.append(content)

    if catalogue_btn_selector == "":
        await page.close()
        return content_list

    soup = BeautifulSoup(content, "lxml")
    catalogue_btn = soup.select(catalogue_btn_selector)
    if len(catalogue_btn) > 0:
        catalogue_next_btn = catalogue_btn[-1]
        catalogue_next_href = catalogue_next_btn["href"]
        if catalogue_next_href == "javascript:void(0);":
            await page.close()
            return content_list
        else:
            return await get_catalogue_page_content(context, urljoin(url, catalogue_next_href), wait_until, content_list, page)

    await page.close()
    return content_list


async def main():
    """入口协程：启动浏览器 → 爬目录 → 批量并发爬正文 → 写文件 → 清理资源。"""
    print("无头浏览器启动中")
    p = await async_playwright().start()
    browser = await p.chromium.launch()
    # browser = p.chromium.launch(headless=False)
    try:
        context = await browser.new_context(
            java_script_enabled=True,
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36",
        )
        context.set_default_timeout(300000.00)

        print("开始获取小说目录列表")
        content_list = await get_catalogue_page_content(context, catalogue_url)
        url_list, novel_title = get_catalogue_url_list(content_list)

        remove_title(novel_title + web_name)

        loop_num = page_config.PRE_LOOP_NUM
        for i in range(ceil(len(url_list[start_num:]) / loop_num)):
            async with asyncio.TaskGroup() as tg:
                tasks_list = [
                    tg.create_task(get_novel_page_content(context, novel_url))
                    for novel_url in url_list[start_num + i * loop_num: start_num + (i + 1) * loop_num]
                ]
                print("开始爬取小说内容")

            success_count = 0
            for task in tasks_list:
                if get_novel_content(task.result(), novel_title):
                    success_count += 1
            print(f"成功 {success_count}/{len(tasks_list)} 章")
            await asyncio.sleep(page_config.PRE_LOOP_WAIT_TIME)
    except Exception as e:
        print(f"爬取出错: {e}")
        raise
    finally:
        await browser.close()
        await p.stop()
    print("无头浏览器已关闭")


if __name__ == "__main__":
    catalogue_url = input("请输入小说目录的url：") or "https://www.bequke.com/xiaoshuo/112/112178/"
    start_num = input("从第几章开始爬取(直接回车默认从头开始):")
    if start_num.isdigit():
        start_num = int(start_num)
    else:
        start_num = 0
    web_name = "_" + urlparse(catalogue_url).netloc.split(".")[-2]
    page_c = page_config.match_url(catalogue_url)
    if page_c:
        catalogue_selector = page_c["catalogue_selector"]
        novel_selector = page_c["novel_selector"]
        catalogue_btn_selector = page_c["catalogue_btn"]
        catalogue_title_selector = page_c["catalogue_title"]
        novel_btn_selector = page_c["novel_btn"]
        if not (catalogue_selector and novel_selector and catalogue_title_selector):
            raise ValueError("选择器为空")
    else:
        catalogue_selector = input("catalogue_selector:(直接回车默认为div#list dd)") or "div#list dd"
        novel_selector = input("novel_selector:(直接回车默认为div#content)") or "div#content"
        catalogue_btn_selector = input("catalogue_btn:(直接回车默认为a.index-container-btn)") or "a.index-container-btn"
        catalogue_title_selector = input("catalogue_title:(直接回车默认为div.info div.top h1)") or "div.info div.top h1"
        novel_btn_selector = input("novel_btn:(直接回车默认为div.section-opt.m-bottom-opt a)") or "div.section-opt.m-bottom-opt a"
    asyncio.run(main())
    input("\n\n爬取成功结束, 回车退出")
