import re
from urllib import parse

import httpx
from bs4 import BeautifulSoup

import agent
from utils import chineseNumber2Int, remove_title, txt_write

catalogue_url = "https://www.dingdianxsw.com/56/56039/"
headers = {"referer": catalogue_url, "user-agent": agent.get_user_agents()}


def get_catalogue_url_list(url):
    resp = httpx.get(url, headers=headers, timeout=None)
    resp.encoding = "gbk"
    catalogue_page = resp.text
    catalogue_soup = BeautifulSoup(catalogue_page, "lxml")
    now_title = catalogue_soup.h1.string
    zhangjie_list = catalogue_soup.find_all("div", class_="zhangjie")
    for i in range(len(zhangjie_list)):
        relative_link = zhangjie_list[i].a["href"]
        zhangjie_list[i] = parse.urljoin(url, relative_link)
    return zhangjie_list, str(now_title)


def get_novel_content(client, url, novel_title):
    resp = client.get(url, timeout=None)
    resp.encoding = "gbk"
    novel_page = resp.text
    novel_soup = BeautifulSoup(novel_page, "lxml")
    if resp.status_code != 200:
        print("目标网址访问失败：")
        raise httpx.ConnectError("\n".join(novel_soup.stripped_strings))
    zhangjie_title = str(novel_soup.h1.string).strip()
    num_pattern = re.compile(r"第(.+?)[章张]")
    title_match = re.match(num_pattern, zhangjie_title)
    if title_match:
        raw_num = title_match.group(1)
        if not raw_num.isdecimal():
            title_num = chineseNumber2Int(raw_num)
            zhangjie_num = f"第{str(title_num)}章"
            zhangjie_title = zhangjie_title.replace(title_match.group(0), zhangjie_num)
    novel_text = novel_soup.find(class_="novel_content").get_text()
    txt_write(novel_title, zhangjie_title, novel_text)


def main():
    url_list, novel_title = get_catalogue_url_list(catalogue_url)
    remove_title(novel_title)
    with httpx.Client(headers=headers) as client:
        for novel_url in url_list:
            get_novel_content(client, novel_url, novel_title)


if __name__ == "__main__":
    main()
