import os
import re
from urllib import parse

import httpx
from bs4 import BeautifulSoup

import agent

catalogue_url = "https://www.dingdianxsw.com/56/56039/"
headers = {"referer": catalogue_url, "user-agent": agent.get_user_agents()}


def mkdir_title(title: str):
    path = os.path.join("books", title)
    if not os.path.exists(path):
        os.makedirs(path)


def chineseNumber2Int(strNum: str):
    result = 0
    temp = 1  # 存放一个单位的数字如：十万
    count = 0  # 判断是否有chArr
    cnArr = ["一", "二", "三", "四", "五", "六", "七", "八", "九"]
    chArr = ["十", "百", "千", "万", "亿"]
    for i in range(len(strNum)):
        b = True
        c = strNum[i]
        for j in range(len(cnArr)):
            if c == cnArr[j]:
                if count != 0:
                    result += temp
                    count = 0
                temp = j + 1
                b = False
                break
        if b:
            for j in range(len(chArr)):
                if c == chArr[j]:
                    if j == 0:
                        temp *= 10
                    elif j == 1:
                        temp *= 100
                    elif j == 2:
                        temp *= 1000
                    elif j == 3:
                        temp *= 10000
                    elif j == 4:
                        temp *= 100000000
                count += 1
        if i == len(strNum) - 1:
            result += temp
    return result


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


def txt_write(novel_title, zhangjie_title, novel_text):
    path = os.path.join("books", f"{novel_title}.txt")
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"{zhangjie_title}\n")
        f.write(novel_text)
    print(zhangjie_title)


def main():
    url_list, novel_title = get_catalogue_url_list(catalogue_url)
    mkdir_title(novel_title)
    with httpx.Client(headers=headers) as client:
        for novel_url in url_list:
            get_novel_content(client, novel_url, novel_title)


if __name__ == "__main__":
    main()
