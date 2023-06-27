import re
from time import sleep
from urllib import parse

from bs4 import BeautifulSoup
from playwright.sync_api import Page, sync_playwright

from utils import chineseNumber2Int, remove_title, txt_write


def get_catalogue_url_list(catalogue_url, content, selector="div#list dd"):
    catalogue_soup = BeautifulSoup(content, "lxml")
    # print(catalogue_soup)
    now_title = catalogue_soup.h1.string
    zhangjie_list = catalogue_soup.select(selector)
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
    num_pattern = re.compile(r"第(.+?)[章张]")
    title_match = re.match(num_pattern, zhangjie_title)
    if title_match:
        raw_num = title_match.group(1)
        if not raw_num.isdecimal():
            title_num = chineseNumber2Int(raw_num)
            zhangjie_num = f"第{str(title_num)}章"
            zhangjie_title = zhangjie_title.replace(title_match.group(0), zhangjie_num)
    novel_text_list = novel_soup.select(selector)
    novel_text = ""
    for novel_tag in novel_text_list:
        novel_text = "\n    ".join((novel_text, novel_tag.get_text()))

    novel_text += "\n\n"
    txt_write(novel_title + web_name, zhangjie_title, novel_text)


def get_page_content(page: Page, url: str):
    resp = page.goto(url)
    assert resp.status == 200
    page.wait_for_load_state("domcontentloaded")
    content = page.content()
    return content


def main(catalogue_url, catalogue_selector="div#list dd", novel_selector="div#content>p"):
    p = sync_playwright().start()
    browser = p.chromium.launch()
    page = browser.new_page()

    content = get_page_content(page, catalogue_url)
    url_list, novel_title = get_catalogue_url_list(catalogue_url, content, catalogue_selector)
    remove_title(novel_title + web_name)
    for novel_url in url_list:
        while True:
            try:
                novel_page = get_page_content(page, novel_url)
                get_novel_content(novel_page, novel_title, novel_selector)
                break
            except AssertionError as e:
                print(e)
                sleep(0.5)
    browser.close()
    p.stop()


if __name__ == "__main__":
    catalogue_url = input("请输入小说目录的url：") or "https://www.biququ.info/html/61746/"
    global web_name
    web_name = "_" + parse.urlparse(catalogue_url).netloc.split(".")[-2]
    catalogue_selector = input("catalogue_selector:(直接回车默认为div#list dd)") or "div#list dd"
    novel_selector = input("novel_selector:(直接回车默认为div#content>p)") or "div#content>p"
    main(catalogue_url, catalogue_selector, novel_selector)
