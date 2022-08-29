import httpx
from bs4 import BeautifulSoup

import agent

from urllib import parse
import asyncio
import re


catalogue_url = "https://www.zhhbqg.com/3_3054/"
headers = {"referer": catalogue_url, "user-agent": agent.get_user_agents()}


def get_catalogue_url_list(url):
    resp = httpx.get(url, headers=headers, timeout=None)
    catalogue_page = resp.text
    catalogue_soup = BeautifulSoup(catalogue_page, "lxml")
    dd_list = catalogue_soup.find_all("dd")
    for i in range(len(dd_list)):
        relative_link = dd_list[i].a["href"]
        dd_list[i] = parse.urljoin(url, relative_link)
    return dd_list


async def get_novel_content(client, url, book_title_set):
    resp = await client.get(url, headers=headers)
    novel_page = resp.text
    novel_soup = BeautifulSoup(novel_page, "lxml")
    novel_text = novel_soup.find(id="content").get_text()
    book_title_compile = re.compile(r"《(.+?)》")
    book_title_list = re.findall(book_title_compile, novel_text)
    book_title_set.update(book_title_list)


async def main():
    url_list = get_catalogue_url_list(catalogue_url)
    book_title_set = set()
    async with httpx.AsyncClient(timeout=None) as client:
        await asyncio.gather(
            *[
                get_novel_content(client, novel_url, book_title_set)
                for novel_url in url_list
            ]
        )
    print(book_title_set)
    with open("book_title.txt", "w", encoding="utf-8") as f:
        book_title_str = "\n".join(book_title_set)
        f.write(book_title_str)


if __name__ == "__main__":
    asyncio.run(main())
