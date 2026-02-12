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
]

def match_url(catalogue_url:str) -> Union[Dict[str, Union[List[str], str]], None]:
    url = urlparse(catalogue_url)
    domain = url.netloc
    for i in range(len(PAGE_CONFIG)):
        page_config = PAGE_CONFIG[i]
        if domain in page_config["domain"]:
            return PAGE_CONFIG[i]
    return None
