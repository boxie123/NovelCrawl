import os


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


def remove_title(title: str):
    path = os.path.join("books", f"{title}.txt")
    if os.path.exists(path):
        os.remove(path)


def txt_write(novel_title, zhangjie_title, novel_text):
    path = os.path.join("books", f"{novel_title}.txt")
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"{zhangjie_title}\n")
        f.write(novel_text)
    print(zhangjie_title)
