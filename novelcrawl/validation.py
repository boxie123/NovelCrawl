"""与界面无关的章节范围校验。"""

from typing import List
from novelcrawl.errors import InvalidChapterSpec

def parse_position_spec(value: str) -> List[int]:
    """把 ``1-3,5`` 解析成有序且去重的目录位置。

    Args:
        value: 用户输入的章节范围表达式。

    Returns:
        正整数位置列表。例如 ``1-3,5`` 返回 ``[1, 2, 3, 5]``。

    Raises:
        InvalidChapterSpec: 表达式为空、包含非数字、倒序范围或非正数。

    先使用 set 去重，最终 sorted 保证结果按位置递增。
    """
    positions = set()
    if not value.strip():
        raise InvalidChapterSpec("章节范围不能为空")

    for segment in value.split(","):
        # 逗号分隔多个独立项目，每个项目可以是单值或闭区间。
        part = segment.strip()
        if not part:
            raise InvalidChapterSpec("章节范围中存在空项目")
        if "-" in part:
            # split(..., 1) 最多切一次；多余的连字符会使右侧不是纯数字，
            # 从而进入无效范围分支。
            pieces = part.split("-", 1)
            if len(pieces) != 2 or not all(piece.isdigit() for piece in pieces):
                raise InvalidChapterSpec("无效章节范围: {}".format(part))
            start, end = (int(piece) for piece in pieces)
            if start <= 0 or end < start:
                raise InvalidChapterSpec("无效章节范围: {}".format(part))
            if end-start+1 > 100000:
                raise InvalidChapterSpec("单次最多选择 100000 章")
            positions.update(range(start, end + 1))
        else:
            if not part.isdigit() or int(part) <= 0:
                raise InvalidChapterSpec("无效章节序号: {}".format(part))
            positions.add(int(part))
        if len(positions) > 100000:
            raise InvalidChapterSpec("单次最多选择 100000 章")
    return sorted(positions)

