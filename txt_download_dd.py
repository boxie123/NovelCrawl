import re
from time import sleep
from urllib import parse

import httpx
from bs4 import BeautifulSoup

import agent
from utils import chineseNumber2Int, remove_title, txt_write

catalogue_url = "https://www.gonb.org/27/27159/"
headers = {"referer": catalogue_url, "user-agent": agent.get_user_agents()}

web_name = "_gonb"


def get_catalogue_url_list(url):
    resp = httpx.get(url, headers=headers, timeout=None)
    resp.encoding = "utf-8"
    catalogue_page = resp.text
    catalogue_soup = BeautifulSoup(catalogue_page, "lxml")
    now_title = catalogue_soup.h1.string
    zhangjie_box = catalogue_soup.find("div", id="list")
    zhangjie_list = zhangjie_box.find_all("dd")
    for i in range(len(zhangjie_list)):
        relative_link = zhangjie_list[i].a["href"]
        zhangjie_list[i] = parse.urljoin(url, relative_link)
    return zhangjie_list, str(now_title)


def get_novel_content(client, url, novel_title):
    resp = client.get(url, timeout=None)
    resp.encoding = "utf-8"
    novel_page = resp.text
    novel_soup = BeautifulSoup(novel_page, "lxml")
    if resp.status_code != 200:
        raise httpx.ConnectError("\n".join(novel_soup.stripped_strings))
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
    novel_text = novel_soup.find(id="content").get_text("\n")
    txt_write(novel_title + web_name, zhangjie_title, novel_text)


def main():
    url_list, novel_title = get_catalogue_url_list(catalogue_url)
    remove_title(novel_title + web_name)
    with httpx.Client(headers=headers) as client:
        for novel_url in url_list:
            while True:
                try:
                    get_novel_content(client, novel_url, novel_title)
                    break
                except httpx.ConnectError as e:
                    print(e)
                    sleep(0.5)


if __name__ == "__main__":
    main()
