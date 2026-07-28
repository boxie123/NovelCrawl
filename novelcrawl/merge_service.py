"""跨来源合并的薄业务层；合并记录只引用来源章节，不复制正文。

这个类本身没有复杂算法，作用是给 CLI 提供清晰的业务名称，并隔离
``Storage`` 与 ``TxtExporter`` 的具体实现。以后增加权限检查或合并规则时，
可以在这里处理而不让 CLI 直接操作 SQL。
"""

from typing import Optional, Sequence, Tuple

from novelcrawl.exporter import TxtExporter
from novelcrawl.storage import Storage


class MergeService:
    """创建合并版本、添加章节引用并触发动态导出。"""

    def __init__(self, storage: Storage, exporter: TxtExporter):
        """保存合并所需的存储层和导出器。"""
        self.storage = storage
        self.exporter = exporter

    def create(
        self,
        name: str,
        *,
        description: Optional[str] = None,
        output_name: Optional[str] = None,
    ) -> int:
        """创建空合并版本并返回其数据库 ID。"""
        return self.storage.create_merged_edition(
            name,
            description=description,
            output_name=output_name,
        )

    def append_range(
        self,
        edition_id: int,
        source_id: int,
        start_position: int,
        end_position: int,
    ) -> int:
        """把来源中的连续目录范围一对一追加到合并版本。"""
        return self.storage.append_source_range(
            edition_id,
            source_id,
            start_position,
            end_position,
        )

    def compose(
        self,
        edition_id: int,
        *,
        title: Optional[str],
        source_positions: Sequence[Tuple[int, int]],
    ) -> int:
        """按输入顺序把多个 ``(来源 ID, 目录位置)`` 组合为一章。"""
        return self.storage.compose_merged_chapter(
            edition_id,
            title=title,
            source_positions=source_positions,
        )

    def export(self, edition_id: int):
        """读取来源章节的当前正文并导出指定合并版本。"""
        return self.exporter.export_merged_edition(edition_id)
