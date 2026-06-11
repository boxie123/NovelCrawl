from typing import List, Dict, Union
from urllib.parse import urlparse

PAGE_CONFIG: List[Dict[str, Union[List[str], str]]] = [
    {
        "domain": ["www.22biqu.com"],
        "catalogue_selector": "div.section-list li",
        "catalogue_btn": "a.index-container-btn",
        "catalogue_title": "div.info div.top h1",
        "novel_selector": "div#content",
        "novel_btn": "div.section-opt.m-bottom-opt a",
    },
    {
        "domain": ["www.sudugu.org"],
        "catalogue_selector": "div#list li",
        "catalogue_btn": "",
        "catalogue_title": "div.itemtxt h1 a",
        "novel_selector": "div.con",
        "novel_btn": "div.prenext a",
    },
    {
        "domain": ["www.bqquge.com"],
        "catalogue_selector": "div#list li",
        "catalogue_btn": "",
        "catalogue_title": "div.booktxt h1",
        "novel_selector": "div.con",
        "novel_btn": "div.prenext a",
    },
    {
        "domain": ["www.minixiaoshuow.com"],
        "catalogue_selector": "div.mod li.cont-li",
        "catalogue_btn": "",
        "catalogue_title": "span.name.fl",
        "novel_selector": "div.main_content",
        "novel_btn": "div.bottom-nav a",
    },
    {
        "domain": ["www.dongtanxs.com"],
        "catalogue_selector": "div.listmain dd",
        "catalogue_btn": "",
        "catalogue_title": "div.book div.info h2",
        "novel_selector": "div#content",
        "novel_btn": "div.page_chapter a",
    },
    {
        "domain": ["www.bequke.com"],
        "catalogue_selector": "div.chapterlist div.all li",
        "catalogue_btn": "",
        "catalogue_title": "div.bookinfo div.booktitle h1 a",
        "novel_selector": "div#booktxt",
        "novel_btn": "div.readpage a",
    },
]

PRE_LOOP_NUM = 3
PRE_LOOP_WAIT_TIME = 10.0


def match_url(catalogue_url: str) -> Union[Dict[str, Union[List[str], str]], None]:
    """根据目录 URL 匹配预设的站点选择器配置。

    Args:
        catalogue_url: 小说目录页 URL。

    Returns:
        Dict 或 None: 匹配到的站点配置字典，未匹配返回 None。
    """
    url = urlparse(catalogue_url)
    domain = url.netloc
    for i in range(len(PAGE_CONFIG)):
        page_config = PAGE_CONFIG[i]
        if domain in page_config["domain"]:
            return PAGE_CONFIG[i]
    return None
