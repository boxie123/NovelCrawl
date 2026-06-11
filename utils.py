import os
from pathlib import Path


def chineseNumber2Int(strNum: str):
    """中文数字转阿拉伯数字，支持简写和繁写。

    Args:
        strNum: 中文数字字符串，如"一百二十三"、"一三三"、"壹佰贰拾"。

    Returns:
        int: 阿拉伯数字。
    """
    cnArr = {"零": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
             "〇": 0, "壹": 1, "贰": 2, "叁": 3, "肆": 4, "伍": 5, "陆": 6, "柒": 7, "捌": 8, "玖": 9, "两": 2}

    chArr = {"十": 10, "百": 100, "千": 1000, "拾": 10, "佰": 100, "仟": 1000}
    magArr = {"万": 10000, "亿": 100000000}

    # 判断是否是简写法, 如"一三三"
    if all(c in cnArr for c in strNum):
        return int("".join(str(cnArr[c]) for c in strNum))

    total = 0
    section = 0
    number = 0
    for c in strNum:
        if c in cnArr:
            number = cnArr[c]
        elif c in chArr:
            unit = chArr[c]
            if number == 0:
                number = 1
            section += number * unit
            number = 0
        elif c in magArr:
            section += number
            total += section * magArr[c]
            section = 0
            number = 0

    return total + section + number


def remove_title(title: str):
    """删除已有的小说 TXT 文件，用于重新爬取前清理。

    Args:
        title: 小说文件名（不含扩展名）。
    """
    path = os.path.join(".", "books", f"{title}.txt")
    if os.path.exists(path):
        os.remove(path)


def txt_write(novel_title, zhangjie_title, novel_text):
    """追加写入章节到 TXT 文件，自动创建 books/ 目录。

    Args:
        novel_title: 小说文件名（不含扩展名）。
        zhangjie_title: 章节标题。
        novel_text: 章节正文内容。
    """
    book_dir_path = Path(os.path.join(".", "books")).resolve()
    if not book_dir_path.exists():
        os.makedirs(str(book_dir_path))

    file_path = book_dir_path.joinpath(f"{novel_title}.txt")
    with open(file_path, "a", encoding="utf-8") as f:
        f.write(f"{zhangjie_title}\n")
        f.write(novel_text)
    print(zhangjie_title)
