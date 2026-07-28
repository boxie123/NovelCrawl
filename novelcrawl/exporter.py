"""把 SQLite 中的数据导出为便于阅读的 TXT 文件。

SQLite 才是正文和状态的真实数据源；TXT 只是随时可以重建的阅读视图。
导出流程不会修改数据库，也不会访问网站：

    查询已排序章节
      → 为成功章节写正文
      → 为失败/未下载章节写可见占位
      → 写入同目录临时文件
      → 原子替换正式 TXT
"""

import os
from pathlib import Path
from typing import Dict, List, Optional

from novelcrawl.storage import Storage


class TxtExporter:
    """负责单来源和合并版本导出，不参与网络抓取。

    Attributes:
        storage: 提供章节查询的 SQLite 数据访问对象。
        source_export_dir: 普通来源 TXT 的目录。
        merged_export_dir: 合并版本 TXT 的目录。
    """

    def __init__(
        self,
        storage: Storage,
        source_export_dir: Path,
        merged_export_dir: Path,
    ):
        """保存导出依赖和路径；此时不会创建任何文件。"""
        self.storage = storage
        self.source_export_dir = Path(source_export_dir)
        self.merged_export_dir = Path(merged_export_dir)

    @staticmethod
    def _atomic_write(output_path: Path, text: str) -> Path:
        """把完整文本安全写入目标路径。

        Args:
            output_path: 最终 TXT 路径。
            text: 已在内存中组装完成的全部文本。

        Returns:
            与 ``output_path`` 相同的路径，便于调用方打印。

        Raises:
            OSError: 目录不可写、磁盘错误或目标文件被其他程序锁定。

        重要：不能直接覆盖正式 TXT。程序若在普通 ``write`` 中途退出，用户
        会得到半个文件。这里先完整写入 ``.tmp``，再一次性替换正式文件。
        """
        # 临时文件与目标文件位于同一目录，os.replace 才能可靠原子替换。
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = output_path.with_suffix(output_path.suffix + ".tmp")
        try:
            with temporary_path.open(
                "w",
                encoding="utf-8",
                newline="\n",
            ) as file:
                file.write(text)
                # flush 把 Python 用户态缓冲交给操作系统，fsync 再要求操作系统
                # 尽量把内容落盘，然后才替换正式文件。
                file.flush()
                os.fsync(file.fileno())
            os.replace(str(temporary_path), str(output_path))
        except Exception:
            # 失败时保留原正式 TXT，只清理本次产生的不完整临时文件。
            if temporary_path.exists():
                temporary_path.unlink()
            raise
        return output_path

    @staticmethod
    def _chapter_title(row) -> str:
        """按优先级选择导出标题，并为无标题章节提供目录位置兜底。"""
        return (
            row["normalized_title"]
            or row["catalogue_title"]
            or "目录第 {} 章".format(row["position"])
        )

    @staticmethod
    def _missing_chapter_text(row) -> str:
        """为 failed 和 pending 章节生成不同的可读占位。"""
        if row["status"] == "failed":
            return (
                "[本章下载失败]\n"
                "目录序号：{position}\n"
                "失败类型：{error_type}\n"
                "失败原因：{error}"
            ).format(
                position=row["position"],
                error_type=row["last_error_type"] or "unknown",
                error=row["last_error"] or "未知错误",
            )
        return (
            "[本章尚未下载]\n"
            "目录序号：{position}"
        ).format(position=row["position"])

    def export_source(
        self,
        source_id: int,
        *,
        start_position: Optional[int] = None,
    ) -> Path:
        """导出一个来源；指定起始位置时不包含更早的目录章节。

        Args:
            source_id: 要导出的来源主键。
            start_position: 可选起点。指定后只查询并导出该位置及以后。

        Returns:
            成功生成的 TXT 路径。

        completed 且正文非空时输出正文；其他状态仍保留章节位置并输出占位，
        让读者能看见缺章，而不是得到看似连续但实际缺失的文件。
        """
        source = self.storage.get_source(source_id)
        rows = self.storage.get_source_export_rows(
            source_id,
            start_position=start_position,
        )
        chunks = []
        for row in rows:
            title = self._chapter_title(row)
            content = (
                row["content"]
                if row["status"] == "completed" and row["content"]
                else self._missing_chapter_text(row)
            )
            # 每章统一为“标题、空行、正文”，rstrip 避免正文尾部换行叠加。
            chunks.append("{}\n\n{}".format(title, content.rstrip()))

        output_path = self.source_export_dir / "{}.txt".format(
            source.output_name
        )
        # 章节之间使用两个换行符，文件非空时再补一个结尾换行。
        text = "\n\n".join(chunks)
        if text:
            text += "\n"
        return self._atomic_write(output_path, text)

    def export_merged_edition(self, edition_id: int) -> Path:
        """按合并章节及 part 顺序，动态读取来源章节的当前正文。

        Args:
            edition_id: 要导出的合并版本主键。

        Returns:
            成功生成的合并版 TXT 路径。

        存储层返回“每个 part 一行”的扁平结果。这里先按
        ``merged_chapter_id`` 分组，再在组内按查询已经保证的 part 顺序拼接。
        重要：正文来自当前 chapters 记录，而非合并时的副本。
        """
        edition = self.storage.get_merged_edition(edition_id)
        rows = self.storage.get_merged_export_rows(edition_id)
        grouped: Dict[int, List] = {}
        for row in rows:
            # setdefault 在键不存在时创建空列表，再把当前 part 加进去。
            grouped.setdefault(int(row["merged_chapter_id"]), []).append(row)

        chunks = []
        for merged_rows in grouped.values():
            first = merged_rows[0]
            title = (
                first["merged_title"]
                or first["normalized_title"]
                or first["catalogue_title"]
                or "合并目录第 {} 章".format(first["merged_position"])
            )
            part_texts = []
            for row in merged_rows:
                # LEFT JOIN 可能得到没有来源 part 的合并章节，需要明确占位。
                if row["source_chapter_id"] is None:
                    part_text = "[合并章节没有配置来源章节]"
                elif row["status"] == "completed" and row["content"]:
                    part_text = row["content"].rstrip()
                elif row["status"] == "failed":
                    part_text = (
                        "[来源章节下载失败]\n"
                        "来源 ID：{source_id}\n"
                        "目录序号：{position}\n"
                        "失败类型：{error_type}\n"
                        "失败原因：{error}"
                    ).format(
                        source_id=row["source_id"],
                        position=row["source_position"],
                        error_type=row["last_error_type"] or "unknown",
                        error=row["last_error"] or "未知错误",
                    )
                else:
                    part_text = (
                        "[来源章节尚未下载]\n"
                        "来源 ID：{source_id}\n"
                        "目录序号：{position}"
                    ).format(
                        source_id=row["source_id"],
                        position=row["source_position"],
                    )

                separator = row["separator"] or ""
                # separator 属于当前 part，第一段通常为空，后续段通常为两个换行。
                part_texts.append("{}{}".format(separator, part_text))

            chunks.append("{}\n\n{}".format(title, "".join(part_texts)))

        output_path = self.merged_export_dir / "{}.txt".format(
            edition.output_name
        )
        text = "\n\n".join(chunks)
        if text:
            text += "\n"
        return self._atomic_write(output_path, text)
