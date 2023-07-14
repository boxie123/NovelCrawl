import os
from pathlib import Path


def chineseNumber2Int(strNum: str):
    result = 0
    cnArr = {"零": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
             "〇": 0, "壹": 1, "贰": 2, "叁": 3, "肆": 4, "伍": 5, "陆": 6, "柒": 7, "捌": 8, "玖": 9, "两": 2}

    chArr = {"十": 10, "百": 100, "千": 1000, "拾": 10, "佰": 100, "仟": 1000}
    magArr = {"万": 10000, "亿": 100000000}

    # 判断是否是简写法, 如"一三三"
    result_str = ""
    for i in range(len(strNum)):
        c = strNum[i]
        if c in chArr:
            break
        elif c in cnArr:
            result_str += str(cnArr[c])
    else:
        return int(result_str)

    # 正常写法
    for i in range(len(strNum)):
        c = strNum[i]
        c_num = cnArr.get(c, 0)
        if i == len(strNum) - 1:
            return result + c_num
        ch = chArr.get(strNum[i + 1], 1)
        magni = magArr.get(strNum[i + 1], 1)
        c_num = c_num * ch
        result = (result + c_num) * magni

    return result


def remove_title(title: str):
    path = os.path.join(".", "books", f"{title}.txt")
    if os.path.exists(path):
        os.remove(path)


def txt_write(novel_title, zhangjie_title, novel_text):
    book_dir_path = Path(os.path.join(".", "books")).resolve()
    if not book_dir_path.exists():
        os.makedirs(str(book_dir_path))

    file_path = book_dir_path.joinpath(f"{novel_title}.txt")
    with open(file_path, "a", encoding="utf-8") as f:
        f.write(f"{zhangjie_title}\n")
        f.write(novel_text)
    print(zhangjie_title)
