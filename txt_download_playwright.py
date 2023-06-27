import re
from time import sleep
from urllib import parse

import httpx
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, Page

import agent
from utils import chineseNumber2Int, remove_title, txt_write

catalogue_url = "https://www.biququ.info/html/61746/"
headers = {
    "referer": catalogue_url,
    "user-agent": agent.get_user_agents()
    }

web_name = "_biququ"


def get_catalogue_url_list(content):
    catalogue_soup = BeautifulSoup(content, "lxml")
    # print(catalogue_soup)
    now_title = catalogue_soup.h1.string
    zhangjie_box = catalogue_soup.find("div", id="list")
    zhangjie_list = zhangjie_box.find_all("dd")
    for i in range(len(zhangjie_list)):
        relative_link = zhangjie_list[i].a["href"]
        zhangjie_list[i] = parse.urljoin(catalogue_url, relative_link)
    return zhangjie_list, now_title


def get_novel_content(novel_page, novel_title):
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
    novel_text_list = novel_soup.select("div#content>p")
    novel_text = ""
    for novel_tag in novel_text_list:
        novel_text = "\n".join((novel_text, novel_tag.get_text()))
        
    novel_text += "\n\n"
    txt_write(novel_title + web_name, zhangjie_title, novel_text)


def get_page_content(page: Page, url: str):
    resp = page.goto(url)
    if resp.status != 200:
        raise httpx.ConnectError
    page.wait_for_load_state('domcontentloaded')
    content = page.content()
    return content


def main():
    p = sync_playwright().start()
    browser = p.chromium.launch()
    page = browser.new_page()
    
    content = get_page_content(page, catalogue_url)
    url_list, novel_title = get_catalogue_url_list(content)
    remove_title(novel_title + web_name)
    for novel_url in url_list:
        while True:
            try:
                novel_page = get_page_content(page, novel_url)
                get_novel_content(novel_page, novel_title)
                break
            except httpx.ConnectError as e:
                print(e)
                sleep(0.5)
    browser.close()
    p.stop()

if __name__ == "__main__":
    main()
