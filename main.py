import re
import asyncio
from typing import List, Dict
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from playwright.async_api import BrowserContext, async_playwright
from utils import chineseNumber2Int, remove_title, txt_write
from page_config import match_url

web_name = ""


def get_catalogue_url_list(content_list):
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


def get_novel_content(novel_page_list, novel_title):
    novel_text = ""
    zhangjie_title = ""
    for i in range(len(novel_page_list)):
        novel_page = novel_page_list[i]["content"]
        novel_soup = BeautifulSoup(novel_page, "lxml")
        if i == 0:
            # zhangjie_title = str(novel_soup.select("div.con p")[0].string).strip()
            zhangjie_title = novel_page_list[i]["title"]
            zhangjie_title_correct = re.compile("正文卷")
            if re.match(zhangjie_title_correct, zhangjie_title):
                zhangjie_title = zhangjie_title[4:]
            num_pattern = re.compile(r"^\s?第?([0-9一二三四五六七八九十百千万亿零壹贰叁肆伍陆柒捌玖拾佰仟]+)[章张]{0,1}")
            title_match = re.match(num_pattern, zhangjie_title)
            if title_match:
                raw_num = title_match.group(1)
                if not raw_num.isdecimal():
                    raw_num = chineseNumber2Int(raw_num)
                zhangjie_num = f"第{str(raw_num)}章"
                zhangjie_title = zhangjie_title.replace(title_match.group(0), zhangjie_num)
        novel_content_tag_list = novel_soup.select(novel_selector)
        if len(novel_content_tag_list) > 0:
            novel_content_tag = novel_soup.select(novel_selector)[0]
        else:
            print(f"{zhangjie_title} 不存在 {novel_selector}")
            print(novel_soup)
            return

        for extract_element in novel_content_tag.find_all("div"):
            extract_element.extract()

        novel_text += novel_content_tag.get_text("\n\t", strip=True)

    text_pattern = re.compile("(正在手打中，请稍等片刻，内容更新后，请重新刷新页面，即可获取最新更新！)|(网页版章节内容慢)")
    if len(novel_text) < 400 and re.search(text_pattern, novel_text):
        print(f"已跳过: {zhangjie_title}")
        return

    novel_text += "\n\n"
    txt_write(novel_title + web_name, zhangjie_title, novel_text)


async def page_goto(page, url, wait_until: str = "domcontentloaded") -> str:
    for _ in range(5):
        try:
            resp = await page.goto(url, wait_until=wait_until)
        except TimeoutError:
            continue
        except TypeError:
            return ""
        if resp.status == 200:
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
    if content_list is None:
        content_list = []
    if page is None:
        page = await context.new_page()
    url = novel_info["href"]
    content = await page_goto(page, url, wait_until)
    content_list.append({
        "content": content,
        "title": novel_info.get("title")
    })

    soup = BeautifulSoup(content, "lxml")
    novel_btn = soup.select(novel_btn_selector)
    if len(novel_btn) > 0:
        novel_next_btn = novel_btn[-1]
        # print(novel_next_btn, "\n", novel_next_btn.string)
        if novel_next_btn.string.strip() == "下一页":
            next_url = urljoin(url, novel_next_btn["href"])
            next_novel_info = {
                "href": next_url,
                "title": novel_info.get("title")
            }
            return await get_novel_page_content(context, next_novel_info, wait_until, content_list, page)
        else:
            return content_list

    return content_list


async def get_catalogue_page_content(
        context: BrowserContext, url: str,
        wait_until: str = "domcontentloaded",
        content_list = None,
        page = None,
) -> List[str]:
    if content_list is None:
        content_list = []
    if page is None:
        page = await context.new_page()

    content = await page_goto(page, url, wait_until)
    content_list.append(content)

    soup = BeautifulSoup(content, "lxml")
    catalogue_btn = soup.select(catalogue_btn_selector)
    if len(catalogue_btn) > 0:
        catalogue_next_btn = catalogue_btn[-1]
        catalogue_next_href = catalogue_next_btn["href"]
        if catalogue_next_href == "javascript:void(0);":
            return content_list
        else:
            return await get_catalogue_page_content(context, urljoin(url, catalogue_next_href), wait_until, content_list, page)

    return content_list


async def main():
    print("无头浏览器启动中")
    p = await async_playwright().start()
    browser = await p.chromium.launch()
    # browser = p.chromium.launch(headless=False)
    context = await browser.new_context(
        java_script_enabled=True,
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36",
    )
    context.set_default_timeout(300000.00)

    print("开始获取小说目录列表")
    content_list = await get_catalogue_page_content(context, catalogue_url)
    url_list, novel_title = get_catalogue_url_list(content_list)

    remove_title(novel_title + web_name)

    loop_num = 100
    for i in range(len(url_list[start_num:]) // loop_num + 1):
        async with asyncio.TaskGroup() as tg:
            tasks_list = [
                tg.create_task(get_novel_page_content(context, novel_url))
                for novel_url in url_list[start_num + i * loop_num: start_num + (i + 1) * loop_num]
            ]
            print("开始爬取小说内容")

        print(f"爬取成功 {len(tasks_list)} 章, 正在写入文件")
        for task in tasks_list:
            get_novel_content(task.result(), novel_title)
        await asyncio.sleep(5.0)

    await browser.close()
    await p.stop()
    print("无头浏览器已关闭")


if __name__ == "__main__":
    catalogue_url = input("请输入小说目录的url：") or "https://www.22biqu.com/biqu81101/"
    start_num = input("从第几章开始爬取(直接回车默认从头开始):")
    if start_num.isdigit():
        start_num = int(start_num)
    else:
        start_num = 0
    web_name = "_" + urlparse(catalogue_url).netloc.split(".")[-2]
    page_c = match_url(catalogue_url)
    if page_c:
        catalogue_selector = page_c["catalogue_selector"]
        novel_selector = page_c["novel_selector"]
        catalogue_btn_selector = page_c["catalogue_btn"] or "a.index-container-btn"
        catalogue_title_selector = page_c["catalogue_title"]
        novel_btn_selector = page_c["novel_btn"] or "div.section-opt.m-bottom-opt a"
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
