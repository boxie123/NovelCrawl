"""无副作用的 HTML 与章节标题解析函数，便于独立测试。

“无副作用”表示这些函数只根据输入计算并返回结果：不访问网络、不写数据库、
不创建文件。测试因此可以直接传入一小段静态 HTML，快速验证网站选择器和
标题规则，而不依赖真实网站。
"""

import re
from typing import List, Optional, Sequence, Tuple
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from novelcrawl.errors import CatalogueParseError
from novelcrawl.models import CatalogueChapter, SiteConfig
from novelcrawl.urls import normalize_url


VOLUME_PATTERN = re.compile("正文卷")
CHAPTER_NUM_PATTERN = re.compile(
    r"^\s?第([0-9一二三四五六七八九十百千万亿零壹贰叁肆伍陆柒捌玖拾佰仟两]+)[章张]?"
)
SKIP_PATTERN = re.compile(
    "(正在手打中，请稍等片刻，内容更新后，请重新刷新页面，即可获取最新更新！)"
    "|(网页版章节内容慢)"
)


def chinese_number_to_int(value: str) -> int:
    """把简体或大写中文数字转换为整数。

    Args:
        value: 例如 ``一百二十三``、``壹佰贰拾叁`` 或逐位写法 ``一二三``。

    Returns:
        对应整数。

    算法把字符分为三类：
        digits: 零到九的数字。
        units: 十、百、千等小单位。
        magnitudes: 万、亿等分节单位。

    逐位写法全部由 digit 构成，可以直接拼接；标准中文写法则通过
    ``number``（当前数字）、``section``（万以内小节）和 ``total`` 累加。
    """
    digits = {
        "零": 0,
        "一": 1,
        "二": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
        "〇": 0,
        "壹": 1,
        "贰": 2,
        "叁": 3,
        "肆": 4,
        "伍": 5,
        "陆": 6,
        "柒": 7,
        "捌": 8,
        "玖": 9,
        "两": 2,
    }
    units = {
        "十": 10,
        "百": 100,
        "千": 1000,
        "拾": 10,
        "佰": 100,
        "仟": 1000,
    }
    magnitudes = {"万": 10_000, "亿": 100_000_000}

    # “一二三”不是“一百二十三”的标准写法，但章节标题中可能出现，
    # 此分支按 1、2、3 直接拼成 123。
    if all(character in digits for character in value):
        return int("".join(str(digits[character]) for character in value))

    total = 0
    section = 0
    number = 0
    for character in value:
        if character in digits:
            number = digits[character]
        elif character in units:
            unit = units[character]
            if number == 0:
                number = 1
            section += number * unit
            number = 0
        elif character in magnitudes:
            # 遇到万/亿时结束当前小节并乘以大单位，再开始下一小节。
            section += number
            total += section * magnitudes[character]
            section = 0
            number = 0

    return total + section + number


def normalize_chapter_title(title: str) -> str:
    """统一章节标题开头的章节号格式。

    Args:
        title: 网站目录提供的原始标题。

    Returns:
        去除 ``正文卷`` 前缀，并把中文章节号转成阿拉伯数字的标题。例如
        ``第一百二十三章 标题`` 变为 ``第123章 标题``。无法识别的标题只
        去除首尾空白，原样保留。
    """
    normalized = title.strip()
    if VOLUME_PATTERN.match(normalized):
        normalized = re.sub(r"^正文卷\s*", "", normalized)

    title_match = CHAPTER_NUM_PATTERN.match(normalized)
    if title_match:
        raw_number = title_match.group(1)
        chapter_number = (
            int(raw_number)
            if raw_number.isdecimal()
            else chinese_number_to_int(raw_number)
        )
        normalized = normalized.replace(
            title_match.group(0),
            "第{}章".format(chapter_number),
            1,
        )
    return normalized


def parse_catalogue(
    html_pages: Sequence[str],
    *,
    catalogue_url: str,
    config: SiteConfig,
) -> Tuple[str, List[CatalogueChapter]]:
    """从目录 HTML 中提取去重后的章节，并从 1 开始记录目录位置。

    Args:
        html_pages: 按网站翻页顺序排列的目录 HTML。
        catalogue_url: 用于把相对章节链接转为绝对 URL 的目录地址。
        config: 当前网站的 CSS 选择器配置。

    Returns:
        二元组：小说名，以及按目录顺序排列的 ``CatalogueChapter`` 列表。

    Raises:
        CatalogueParseError: 页面为空、标题选择器失效或没有解析出章节。

    URL 是章节去重依据。同一章节若在分页边界重复出现，只保留第一次，
    ``position`` 也只对真正加入的章节递增。
    """
    if not html_pages:
        raise CatalogueParseError("目录页面为空")

    # 小说标题通常只需要从第一页提取。
    first_soup = BeautifulSoup(html_pages[0], "lxml")
    title_node = first_soup.select_one(config.catalogue_title_selector)
    if title_node is None:
        raise CatalogueParseError(
            "无法通过选择器 {!r} 获取小说名".format(
                config.catalogue_title_selector
            )
        )
    novel_title = title_node.get_text(strip=True)
    if not novel_title:
        raise CatalogueParseError("小说名为空")

    chapters = []
    seen_urls = set()
    position = 1
    for html in html_pages:
        soup = BeautifulSoup(html, "lxml")
        for item in soup.select(config.catalogue_selector):
            anchor = item.find("a")
            if anchor is None:
                continue
            href = anchor.get("href")
            title = anchor.get_text(strip=True)
            if not href or not title:
                continue
            absolute_url = normalize_url(urljoin(catalogue_url, href))
            # 标题可能重复或改名，因此不能用标题作为章节身份。
            if absolute_url in seen_urls:
                continue
            seen_urls.add(absolute_url)
            chapters.append(
                CatalogueChapter(
                    url=absolute_url,
                    title=title,
                    normalized_title=normalize_chapter_title(title),
                    position=position,
                )
            )
            position += 1

    if not chapters:
        raise CatalogueParseError(
            "无法通过选择器 {!r} 获取章节目录".format(
                config.catalogue_selector
            )
        )
    return novel_title, chapters


def parse_chapter_text(html: str, selector: str) -> Optional[str]:
    """提取正文，并移除正文容器中的嵌套 div 广告块。

    Args:
        html: 单个正文分页的 HTML。
        selector: 定位正文容器的 CSS 选择器。

    Returns:
        清理后的正文；选择器未匹配时返回 ``None``。

    ``get_text`` 用换行符连接文本节点，并以制表符缩进新段落。嵌套 div
    按当前站点经验通常是广告或提示块，因此先从解析树中移除。
    """
    soup = BeautifulSoup(html, "lxml")
    content_node = soup.select_one(selector)
    if content_node is None:
        return None

    for child_div in content_node.find_all("div"):
        child_div.extract()
    return content_node.get_text("\n\t", strip=True)


def validate_chapter_content(content: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """检查解析结果能否作为完整章节入库。

    Args:
        content: 已拼接全部分页的正文，或 ``None``。

    Returns:
        ``(error_type, error_message)``。两个值都为 ``None`` 表示验证通过；
        否则调用方应把本章记录为失败，不能保存为 completed。

    较短正文不一定有问题，因此只有“短且命中已知风控提示”才判定为
    ``blocked_content``。
    """
    if not content:
        return "empty_content", "正文为空或正文选择器未匹配"
    if len(content) < 400 and SKIP_PATTERN.search(content):
        return "blocked_content", "页面返回风控或待更新提示"
    return None, None
