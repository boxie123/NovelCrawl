"""流式 TXT 导出：只读取 SQLite，临时文件完成后原子替换。"""

import os
import tempfile
from pathlib import Path

from novelcrawl.control import RunControl


class TxtExporter:
    def __init__(self, storage, source_export_dir, merged_export_dir, *, control=None):
        self.storage = storage
        self.source_export_dir = Path(source_export_dir)
        self.merged_export_dir = Path(merged_export_dir)
        self.control = control or RunControl()

    @staticmethod
    def _atomic_write(output_path, chunks, control=None):
        """停止发生在提交许可前时保留旧 TXT；最终提交不持有事件循环锁。"""
        control = control or RunControl()
        control.check()
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=output_path.name+'.', suffix='.tmp', dir=output_path.parent)
        temporary_path = Path(name)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as file:
                pieces = [chunks] if isinstance(chunks, str) else chunks
                for chunk in pieces:
                    # 单章也可能很大，限制每次写入并检查停止。
                    for offset in range(0, len(chunk), 65536):
                        control.check()
                        file.write(chunk[offset:offset+65536])
                control.check()
                file.flush()
                os.fsync(file.fileno())
            control.reserve_commit()
            os.replace(temporary_path, output_path)
        finally:
            temporary_path.unlink(missing_ok=True)
        return output_path

    @staticmethod
    def _chapter_title(row):
        return row['normalized_title'] or row['catalogue_title'] or f"目录第 {row['position']} 章"

    @staticmethod
    def _missing_chapter_text(row):
        if row['status'] == 'failed':
            return ('[本章下载失败]\n目录序号：{position}\n失败类型：{error_type}\n失败原因：{error}').format(
                position=row['position'], error_type=row['last_error_type'] or 'unknown',
                error=row['last_error'] or '未知错误')
        return f"[本章尚未下载]\n目录序号：{row['position']}"

    def export_source(self, source_id, *, start_position=None):
        source = self.storage.get_source(source_id)
        def chunks():
            first = True
            for row in self.storage.iter_source_export(source_id, start_position):
                self.control.check()
                if not first:
                    yield '\n'
                first = False
                yield self._chapter_title(row)+'\n\n'
                content = row['content'] if row['status'] == 'completed' and row['content'] else self._missing_chapter_text(row)
                yield content.rstrip()+'\n'
        return self._atomic_write(self.source_export_dir/(source.output_name+'.txt'), chunks(), self.control)

    def export_merged_edition(self, edition_id):
        edition = self.storage.get_merged_edition(edition_id)
        def chunks():
            previous = None
            for row in self.storage.iter_merged_export(edition_id):
                self.control.check()
                if row['merged_chapter_id'] != previous:
                    if previous is not None:
                        yield '\n\n'
                    previous = row['merged_chapter_id']
                    yield (row['merged_title'] or row['normalized_title'] or row['catalogue_title'] or
                           f"合并目录第 {row['merged_position']} 章")+'\n\n'
                if row['source_chapter_id'] is None:
                    content = '[合并章节没有配置来源章节]'
                elif row['status'] == 'completed' and row['content']:
                    content = row['content'].rstrip()
                elif row['status'] == 'failed':
                    content = ('[来源章节下载失败]\n来源 ID：{source_id}\n目录序号：{source_position}\n'
                               '失败类型：{error_type}\n失败原因：{error}').format(
                        source_id=row['source_id'], source_position=row['source_position'],
                        error_type=row['last_error_type'] or 'unknown', error=row['last_error'] or '未知错误')
                else:
                    content = f"[来源章节尚未下载]\n来源 ID：{row['source_id']}\n目录序号：{row['source_position']}"
                yield (row['separator'] or '')+content
            if previous is not None:
                yield '\n'
        return self._atomic_write(self.merged_export_dir/(edition.output_name+'.txt'), chunks(), self.control)
