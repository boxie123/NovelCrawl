import asyncio
import re
from urllib import parse

import httpx
from bs4 import BeautifulSoup

import agent

catalogue_url = "https://www.zhhbqg.com/3_3054/"
headers = {"referer": catalogue_url, "user-agent": agent.get_user_agents()}


def get_catalogue_url_list(url):
    resp = httpx.get(url, headers=headers, timeout=None)
    resp.encoding = "gbk"
    catalogue_page = resp.text
    catalogue_soup = BeautifulSoup(catalogue_page, "lxml")
    now_title = catalogue_soup.h1.string
    dd_list = catalogue_soup.find_all("dd")
    for i in range(len(dd_list)):
        relative_link = dd_list[i].a["href"]
        dd_list[i] = parse.urljoin(url, relative_link)
    return dd_list, str(now_title)


async def get_novel_content(
    client, url, book_title_list: list, book_recommend_list: list, now_title: str
):
    resp = await client.get(url, headers=headers)
    novel_page = resp.text
    novel_soup = BeautifulSoup(novel_page, "lxml")
    novel_text = novel_soup.find(id="content").get_text()
    text_list = novel_text.split()
    book_title_compile = re.compile(r"《(.+?)》")
    # book_recommend_compile = re.compile(r"推.*?书")
    brackets_compile = re.compile(r"（(.+)）")
    for paragraph in text_list:
        brackets_match = re.search(brackets_compile, paragraph)
        if brackets_match:
            brackets_text = brackets_match.group(1)
            book_title_match = re.search(book_title_compile, brackets_text)
            if book_title_match:
                book_title = book_title_match.group(1)
                if book_title != now_title:
                    book_title_list.append(book_title)
                    book_recommend_list.append(brackets_text)


async def main():
    url_list, now_title = get_catalogue_url_list(catalogue_url)
    book_title_list = []
    book_recommend_list = []
    async with httpx.AsyncClient(max_redirects=60, timeout=None) as client:
        await asyncio.gather(
            *[
                get_novel_content(
                    client, novel_url, book_title_list, book_recommend_list, now_title
                )
                for novel_url in url_list
            ]
        )
    print(book_title_list)
    with open("book_title.txt", "w", encoding="utf-8") as f:
        book_title_str = "\n".join(book_title_list)
        f.write(book_title_str)

    with open("book_recommend.txt", "w", encoding="utf-8") as f:
        book_recommend_str = "\n".join(book_recommend_list)
        f.write(book_recommend_str)


if __name__ == "__main__":
    asyncio.run(main())
