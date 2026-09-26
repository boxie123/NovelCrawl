"""受支持站点的 CSS 选择器配置及 URL 匹配。

网站 HTML 结构不同，但抓取流程相同。新增站点通常只需添加一个
``SiteConfig``，不用复制下载、数据库或导出代码。空的翻页选择器表示相应
页面只有一页。
"""

from typing import Dict, Optional
from urllib.parse import urlparse

from novelcrawl.models import SiteConfig


SITE_CONFIGS: Dict[str, SiteConfig] = {
    "22biqu": SiteConfig(
        key="22biqu",
        domains=("www.22biqu.com",),
        catalogue_selector="div.section-list li",
        catalogue_button_selector="a.index-container-btn",
        catalogue_title_selector="div.info div.top h1",
        novel_selector="div#content",
        novel_button_selector="div.section-opt.m-bottom-opt a",
    ),
    "sudugu": SiteConfig(
        key="sudugu",
        domains=("www.sudugu.org", "www.shudugu.org"),
        catalogue_selector="div#list li",
        catalogue_button_selector="",
        catalogue_title_selector="div.itemtxt h1 a",
        novel_selector="div.con",
        novel_button_selector="div.prenext a",
    ),
    "bqquge": SiteConfig(
        key="bqquge",
        domains=("www.bqquge.com",),
        catalogue_selector="div#list li",
        catalogue_button_selector="",
        catalogue_title_selector="div.booktxt h1",
        novel_selector="div.con",
        novel_button_selector="div.prenext a",
    ),
    "minixiaoshuow": SiteConfig(
        key="minixiaoshuow",
        domains=("www.minixiaoshuow.com",),
        catalogue_selector="div.mod li.cont-li",
        catalogue_button_selector="",
        catalogue_title_selector="span.name.fl",
        novel_selector="div.main_content",
        novel_button_selector="div.bottom-nav a",
    ),
    "dongtanxs": SiteConfig(
        key="dongtanxs",
        domains=("www.dongtanxs.com",),
        catalogue_selector="div.listmain dd",
        catalogue_button_selector="",
        catalogue_title_selector="div.book div.info h2",
        novel_selector="div#content",
        novel_button_selector="div.page_chapter a",
    ),
    "bequke": SiteConfig(
        key="bequke",
        domains=("www.bequke.com",),
        catalogue_selector="div.chapterlist div.all li",
        catalogue_button_selector="",
        catalogue_title_selector="div.bookinfo div.booktitle h1 a",
        novel_selector="div#booktxt",
        novel_button_selector="div.readpage a",
    ),
    "1qxs": SiteConfig(
        key="1qxs",
        domains=("www.1qxs.com",),
        catalogue_selector="div.catalog div.list ul li",
        catalogue_button_selector="",
        catalogue_title_selector="div.book h1 a",
        novel_selector="div.read div.content",
        novel_button_selector="div.page a",
    ),
}


def match_site_config(url: str) -> Optional[SiteConfig]:
    """按 URL 域名查找抓取规则。

    Args:
        url: 已规范化或普通的绝对 URL。

    Returns:
        匹配的 ``SiteConfig``；不支持该域名时返回 ``None``。
    """
    domain = (urlparse(url).hostname or "").lower()
    for config in SITE_CONFIGS.values():
        if domain in config.domains:
            return config
    return None


def get_site_config(key: str) -> Optional[SiteConfig]:
    """按数据库保存的稳定 key 取回站点配置。"""
    return SITE_CONFIGS.get(key)
