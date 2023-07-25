import re
import asyncio
from urllib import parse

from bs4 import BeautifulSoup
from playwright.async_api import BrowserContext, async_playwright

from utils import chineseNumber2Int, remove_title, txt_write


def get_catalogue_url_list(catalogue_url, content, selector="div#list dd"):
    catalogue_soup = BeautifulSoup(content, "lxml")
    # print(catalogue_soup)
    try:
        now_title = catalogue_soup.h1.string
    except AttributeError:
        now_title = input("获取小说名失败, 请手动输入:")
    zhangjie_list = catalogue_soup.select(selector)
    print(f"共获取到 {len(zhangjie_list)} 章小说链接")
    for i in range(len(zhangjie_list)):
        relative_link = zhangjie_list[i].a["href"]
        zhangjie_list[i] = parse.urljoin(catalogue_url, relative_link)
    return zhangjie_list, now_title


def get_novel_content(novel_page, novel_title, selector="div#content>p"):
    novel_soup = BeautifulSoup(novel_page, "lxml")
    zhangjie_title = str(novel_soup.h1.string).strip()
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
    novel_text_list = novel_soup.select(selector)
    novel_text = ""
    for novel_tag in novel_text_list:
        novel_text = "\n    ".join((novel_text, novel_tag.get_text()))

    text_pattern = re.compile("(正在手打中，请稍等片刻，内容更新后，请重新刷新页面，即可获取最新更新！)|(网页版章节内容慢)")
    if len(novel_text) < 400 and re.search(text_pattern, novel_text):
        print(f"已跳过: {zhangjie_title}")
        return

    novel_text += "\n\n"
    txt_write(novel_title + web_name, zhangjie_title, novel_text)


async def get_page_content(context: BrowserContext, url: str, wait_until: str = "domcontentloaded"):
    page = await context.new_page()
    resp = await page.goto(url)
    assert resp.status == 200
    await page.wait_for_load_state(wait_until)
    content = await page.content()
    await page.close()
    return content


async def main(catalogue_url, start_num=0, catalogue_selector="div#list dd", novel_selector="div#content>p"):
    print("无头浏览器启动中")
    p = await async_playwright().start()
    browser = await p.chromium.launch()
    # browser = p.chromium.launch(headless=False)
    context = await browser.new_context(java_script_enabled=True, user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36")
    context.set_default_timeout(30000.00)

    print("开始获取小说目录列表")
    content = await get_page_content(context, catalogue_url, "networkidle")
    url_list, novel_title = get_catalogue_url_list(catalogue_url, content, catalogue_selector)

    remove_title(novel_title + web_name)
    async with asyncio.TaskGroup() as tg:
        tasks_list = [tg.create_task(get_page_content(context, novel_url)) for novel_url in url_list[start_num:]]
        print("开始爬取小说内容")

    print("爬取成功, 正在写入文件")
    for task in tasks_list:
        get_novel_content(task.result(), novel_title, novel_selector)

    await browser.close()
    await p.stop()
    print("无头浏览器已关闭")


if __name__ == "__main__":
    catalogue_url = input("请输入小说目录的url：") or "https://www.biququ.info/html/61746/"
    start_num = input("从第几章开始爬取(直接回车默认从头开始):")
    if start_num.isdigit():
        start_num = int(start_num)
    else:
        start_num = 0
    global web_name
    web_name = "_" + parse.urlparse(catalogue_url).netloc.split(".")[-2]
    catalogue_selector = input("catalogue_selector:(直接回车默认为div#list dd)") or "div#list dd"
    novel_selector = input("novel_selector:(直接回车默认为div#content>p)") or "div#content>p"
    asyncio.run(main(catalogue_url, start_num, catalogue_selector, novel_selector))
    input("\n\n爬取成功结束, 回车退出")
