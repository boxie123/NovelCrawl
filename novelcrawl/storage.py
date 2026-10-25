"""SQLite 数据访问层。

这里是正文和下载状态的唯一持久化入口；爬虫与导出模块不直接执行 SQL。

核心表关系：

    sources（一个网站上的一本小说）
      └── chapters（该来源的目录章节与正文）

    merged_editions（用户创建的合并版本）
      └── merged_chapters（合并版中的章节和顺序）
            └── merged_chapter_parts（按顺序引用一个或多个 chapters）

合并表只保存外键引用，不复制正文。因此来源章节刷新后，合并版下次导出会
自然读取新正文。所有改变正文或状态的方法都使用 SQLite 事务（transaction），
保证程序中断时不会留下“正文已写入但状态未完成”之类的半完成记录。
"""

import hashlib
import re
import sqlite3
from dataclasses import asdict
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

from novelcrawl.errors import (
    ChapterNotFound,
    MergeEditionNotFound,
    SourceNotFound,
    UnsupportedDatabaseVersion,
)
from novelcrawl.models import (
    CatalogueChapter,
    ChapterRecord,
    MergedEdition,
    Progress,
    SourceRecord,
)


DATABASE_VERSION = 1
# Windows 文件名禁止的字符，同时排除 ASCII 控制字符。
INVALID_FILENAME_PATTERN = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
# 这些名称是 Windows 为历史 DOS 设备保留的路径别名。判断不区分大小写，
# 并且扩展名不能规避限制，例如 CON、con.txt 都不能作为普通文件创建。
WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    "CLOCK$",
    "CONIN$",
    "CONOUT$",
    *{"COM{}".format(number) for number in range(1, 10)},
    *{"LPT{}".format(number) for number in range(1, 10)},
    # Windows 也把 ISO-8859-1 上标数字 ¹、²、³ 识别为设备编号。
    *{"COM{}".format(number) for number in ("¹", "²", "³")},
    *{"LPT{}".format(number) for number in ("¹", "²", "³")},
}

# 第一版直接创建完整结构；未来升级时再按 user_version 增加迁移。
#
# sources:
#   保存来源身份和稳定输出名。catalogue_url 唯一，防止同一来源重复创建。
# chapters:
#   保存目录位置、下载状态和完整正文。(source_id, chapter_url) 唯一，
#   因为 URL 比可能改名或重复的章节标题更适合充当章节身份。
# merged_*:
#   把“合并版的显示顺序”与“来源正文”分开，允许一章引用多段来源章节。
SCHEMA = """
CREATE TABLE sources (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    catalogue_url       TEXT NOT NULL UNIQUE,
    title               TEXT NOT NULL,
    site_name           TEXT NOT NULL,
    site_config_key     TEXT NOT NULL,
    output_name         TEXT NOT NULL,
    created_at          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE chapters (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id           INTEGER NOT NULL,
    chapter_url         TEXT NOT NULL,
    position            INTEGER NOT NULL,
    catalogue_title     TEXT,
    normalized_title    TEXT,
    content             TEXT,
    content_hash        TEXT,
    status              TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'completed', 'failed')),
    attempt_count       INTEGER NOT NULL DEFAULT 0,
    last_error_type     TEXT,
    last_error          TEXT,
    last_attempt_at     TEXT,
    fetched_at          TEXT,
    created_at          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (source_id) REFERENCES sources(id) ON DELETE RESTRICT,
    UNIQUE (source_id, chapter_url)
);

CREATE INDEX idx_chapters_source_position
ON chapters(source_id, position);

CREATE INDEX idx_chapters_download_queue
ON chapters(source_id, status, position);

CREATE TABLE merged_editions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL UNIQUE,
    output_name     TEXT NOT NULL,
    description     TEXT,
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE merged_chapters (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    edition_id          INTEGER NOT NULL,
    position            INTEGER NOT NULL,
    title               TEXT,
    FOREIGN KEY (edition_id) REFERENCES merged_editions(id) ON DELETE CASCADE,
    UNIQUE (edition_id, position)
);

CREATE TABLE merged_chapter_parts (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    merged_chapter_id   INTEGER NOT NULL,
    source_chapter_id   INTEGER NOT NULL,
    part_order          INTEGER NOT NULL,
    separator           TEXT NOT NULL DEFAULT '\n\n',
    FOREIGN KEY (merged_chapter_id)
        REFERENCES merged_chapters(id) ON DELETE CASCADE,
    FOREIGN KEY (source_chapter_id)
        REFERENCES chapters(id) ON DELETE RESTRICT,
    UNIQUE (merged_chapter_id, part_order)
);
"""


def sanitize_filename(value: str) -> str:
    """把用户或网站提供的名称转换为可安全使用的文件名。

    Args:
        value: 原始小说名、站点名或合并版本名。

    Returns:
        去除首尾空白、末尾句点并替换非法字符后的名称。若结果为空，则使用
        ``未命名``。若名称命中 Windows 保留设备名，则在前面添加下划线。
    """
    sanitized = INVALID_FILENAME_PATTERN.sub("_", value).strip().rstrip(".")
    sanitized = sanitized or "未命名"
    # Windows 以第一个句点前的部分判断设备名，因此 CON.txt 也需要处理。
    basename = sanitized.split(".", 1)[0].upper()
    if basename in WINDOWS_RESERVED_NAMES:
        sanitized = "_{}".format(sanitized)
    return sanitized


class Storage:
    """管理数据库连接，并提供按业务语义封装的读写方法。

    正常用法是上下文管理器：

    .. code-block:: python

        with Storage(path) as storage:
            sources = storage.list_sources()

    进入 ``with`` 时自动打开并初始化数据库，退出时即使发生异常也会关闭
    连接。测试也使用这种明确的生命周期，避免连接泄漏。
    """

    def __init__(self, db_path: Path, *, read_only: bool = False):
        """记录数据库路径；实际连接延迟到 ``open`` 时创建。"""
        self.db_path = Path(db_path)
        self.read_only = read_only
        self.connection: Optional[sqlite3.Connection] = None

    def open(self) -> None:
        """创建父目录、打开 SQLite 并设置连接级选项。

        ``row_factory`` 让查询结果既能按下标访问，也能使用 ``row["title"]``
        这种更易读的列名访问方式。

        PRAGMA 说明：
            foreign_keys: 启用 SQLite 默认关闭的外键约束。
            journal_mode=WAL: 使用预写日志，提高读写并存时的稳定性。
            busy_timeout: 数据库短暂被占用时最多等待 5 秒，而非立即报错。
        """
        if self.read_only:
            connection = sqlite3.connect(self.db_path.resolve().as_uri() + "?mode=ro", uri=True)
        else:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(str(self.db_path))
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        if not self.read_only:
            connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = 5000")
        self.connection = connection

    def close(self) -> None:
        """关闭已打开连接；重复调用是安全的。"""
        if self.connection is not None:
            self.connection.close()
            self.connection = None

    def __enter__(self):
        """实现 ``with Storage(...) as storage`` 的进入逻辑。"""
        self.open()
        try:
            self.initialize()
        except BaseException:
            self.close()
            raise
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        """离开 ``with`` 时关闭连接，不吞掉调用方异常。"""
        self.close()

    def _connection(self) -> sqlite3.Connection:
        """返回活动连接；忘记打开数据库时尽早给出明确错误。"""
        if self.connection is None:
            raise RuntimeError("数据库尚未打开")
        return self.connection

    def initialize(self) -> None:
        """初始化新数据库，或确认已有数据库版本受支持。

        Raises:
            UnsupportedDatabaseVersion: 数据库不是当前代码支持的版本。

        ``PRAGMA user_version`` 是 SQLite 文件内置的整数版本标记。当前第一版
        不执行自动迁移，因此版本不匹配时宁可停止，也不冒险读写未知结构。
        """
        connection = self._connection()
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version == 0 and not self.read_only:
            # executescript 用于一次执行整份建表脚本。首次建库若因磁盘、权限
            # 或 SQL 错误中断，SQLite 不保证此前所有 DDL 都自动回滚，可能
            # 留下不完整的新数据库；当前第一版不做迁移或自动修复。
            with connection:
                connection.executescript(SCHEMA)
                connection.execute(
                    "PRAGMA user_version = {}".format(DATABASE_VERSION)
                )
        elif version != DATABASE_VERSION:
            raise UnsupportedDatabaseVersion(
                "数据库版本为 {}，当前程序仅支持版本 {}".format(
                    version,
                    DATABASE_VERSION,
                )
            )

    def _make_unique_output_name(
        self,
        base_name: str,
        *,
        table: str,
    ) -> str:
        """生成在指定表中尚未使用的安全输出名。

        Args:
            base_name: 用户期望的基础名称。
            table: 包含 ``output_name`` 列的内部表名。

        Returns:
            可直接用作文件名且在该表中唯一的名称。冲突时依次添加 ``_2``、
            ``_3`` 等后缀。

        ``table`` 只由本模块传入固定表名，不接受 GUI 原始输入；查询值仍使用
        ``?`` 参数绑定，避免把文本拼进 SQL。
        """
        connection = self._connection()
        safe_base = sanitize_filename(base_name)
        candidate = safe_base
        suffix = 2
        while connection.execute(
            "SELECT 1 FROM {} WHERE output_name = ?".format(table),
            (candidate,),
        ).fetchone():
            candidate = "{}_{}".format(safe_base, suffix)
            suffix += 1
        return candidate

    def upsert_source(
        self,
        *,
        catalogue_url: str,
        title: str,
        site_name: str,
        site_config_key: str,
        output_name: str,
    ) -> int:
        """创建来源或更新来源元数据，已有来源保持原输出文件名。

        Args:
            catalogue_url: 已规范化的目录 URL，也是来源唯一身份。
            title: 本次目录解析得到的小说名。
            site_name: 用于显示和默认文件名的简短站点名。
            site_config_key: 指向 ``site_config.py`` 中的配置键。
            output_name: 新来源期望使用的输出名。

        Returns:
            新建或已有来源的数据库主键。

        ``upsert`` 表示“存在则更新，不存在则插入”。已有来源不更新输出名，
        避免网站改名后导出路径突然变化。
        """
        connection = self._connection()
        existing = connection.execute(
            "SELECT id FROM sources WHERE catalogue_url = ?",
            (catalogue_url,),
        ).fetchone()

        with connection:
            if existing:
                # 这里只更新网站可能变化的元数据，不修改来源身份和输出路径。
                connection.execute(
                    """
                    UPDATE sources
                    SET title = ?,
                        site_name = ?,
                        site_config_key = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (title, site_name, site_config_key, existing["id"]),
                )
                return int(existing["id"])

            unique_output_name = self._make_unique_output_name(
                output_name,
                table="sources",
            )
            cursor = connection.execute(
                """
                INSERT INTO sources (
                    catalogue_url,
                    title,
                    site_name,
                    site_config_key,
                    output_name
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    catalogue_url,
                    title,
                    site_name,
                    site_config_key,
                    unique_output_name,
                ),
            )
            return int(cursor.lastrowid)

    def get_source(self, source_id: int) -> SourceRecord:
        """按主键取得来源。

        Raises:
            SourceNotFound: 指定来源不存在。
        """
        row = self._connection().execute(
            "SELECT * FROM sources WHERE id = ?",
            (source_id,),
        ).fetchone()
        if row is None:
            raise SourceNotFound("来源 {} 不存在".format(source_id))
        return self._source_from_row(row)

    def list_sources(self) -> List[SourceRecord]:
        """按创建顺序返回所有来源，供 ``status`` 命令展示。"""
        rows = self._connection().execute(
            "SELECT * FROM sources ORDER BY id"
        ).fetchall()
        return [self._source_from_row(row) for row in rows]

    @staticmethod
    def _source_from_row(row: sqlite3.Row) -> SourceRecord:
        """把 SQLite Row 转为模块间通用的只读数据对象。"""
        return SourceRecord(
            id=int(row["id"]),
            catalogue_url=row["catalogue_url"],
            title=row["title"],
            site_name=row["site_name"],
            site_config_key=row["site_config_key"],
            output_name=row["output_name"],
        )

    def sync_catalogue(
        self,
        source_id: int,
        chapters: Sequence[CatalogueChapter],
    ) -> None:
        """同步最新目录，但绝不重置已完成章节的正文和状态。

        Args:
            source_id: 目录所属来源。
            chapters: 本次从网站解析出的完整章节序列。

        Raises:
            SourceNotFound: 来源不存在。

        重要：冲突更新只涉及位置和标题，不更新 ``status``、``content`` 或
        错误字段。这样重复执行同步不会把 completed 章节重置为 pending。
        """
        connection = self._connection()
        self.get_source(source_id)
        values = [
            (
                source_id,
                chapter.url,
                chapter.position,
                chapter.title,
                chapter.normalized_title,
            )
            for chapter in chapters
        ]
        # 整份目录使用一个事务，避免程序中断后只同步了一部分目录。
        with connection:
            # executemany 用同一条 UPSERT 批量绑定所有章节参数。
            connection.executemany(
                """
                INSERT INTO chapters (
                    source_id,
                    chapter_url,
                    position,
                    catalogue_title,
                    normalized_title,
                    status
                )
                VALUES (?, ?, ?, ?, ?, 'pending')
                ON CONFLICT(source_id, chapter_url) DO UPDATE SET
                    position = excluded.position,
                    catalogue_title = excluded.catalogue_title,
                    normalized_title = excluded.normalized_title,
                    updated_at = CURRENT_TIMESTAMP
                """,
                values,
            )

    def get_download_queue(
        self,
        source_id: int,
        *,
        start_position: Optional[int] = None,
    ) -> List[ChapterRecord]:
        """取得本次续传队列：新章节优先，然后重试失败章节。

        Args:
            source_id: 要续传的来源。
            start_position: 可选起点；更早章节不进入本次队列。

        Returns:
            按“pending 优先、failed 其次，各状态内按目录位置”排序的章节。

        completed 不会出现在普通队列中，只有显式 ``refresh`` 才会重抓。
        """
        parameters = [source_id]
        position_clause = ""
        if start_position is not None:
            # SQL 片段本身固定，仅起始数值通过参数绑定传入。
            position_clause = "AND position >= ?"
            parameters.append(start_position)

        rows = self._connection().execute(
            """
            SELECT *
            FROM chapters
            WHERE source_id = ?
              AND status IN ('pending', 'failed')
              {}
            ORDER BY
                CASE status
                    WHEN 'pending' THEN 0
                    WHEN 'failed' THEN 1
                END,
                position
            """.format(position_clause),
            parameters,
        ).fetchall()
        return [self._chapter_from_row(row) for row in rows]

    def get_chapters_by_positions(
        self,
        source_id: int,
        positions: Optional[Iterable[int]] = None,
    ) -> List[ChapterRecord]:
        """取得全部章节或一组指定目录位置，主要供刷新功能使用。

        Args:
            source_id: 章节所属来源。
            positions: 目录位置集合；``None`` 表示全部，空集合表示没有章节。

        Returns:
            按目录位置排序的章节记录。
        """
        if positions is None:
            rows = self._connection().execute(
                "SELECT * FROM chapters WHERE source_id = ? ORDER BY position",
                (source_id,),
            ).fetchall()
        else:
            unique_positions = sorted(set(positions))
            if not unique_positions:
                return []
            # SQLite 的 IN 子句需要与位置数量相同的问号占位符。
            placeholders = ",".join("?" for _ in unique_positions)
            rows = self._connection().execute(
                """
                SELECT *
                FROM chapters
                WHERE source_id = ?
                  AND position IN ({})
                ORDER BY position
                """.format(placeholders),
                [source_id] + unique_positions,
            ).fetchall()
        return [self._chapter_from_row(row) for row in rows]

    def get_chapter(self, chapter_id: int) -> ChapterRecord:
        """按主键取得单章，不存在时抛出 ``ChapterNotFound``。"""
        row = self._connection().execute(
            "SELECT * FROM chapters WHERE id = ?",
            (chapter_id,),
        ).fetchone()
        if row is None:
            raise ChapterNotFound("章节 {} 不存在".format(chapter_id))
        return self._chapter_from_row(row)

    @staticmethod
    def _chapter_from_row(row: sqlite3.Row) -> ChapterRecord:
        """把数据库章节行转换为抓取层使用的 ``ChapterRecord``。"""
        return ChapterRecord(
            id=int(row["id"]),
            source_id=int(row["source_id"]),
            url=row["chapter_url"],
            position=int(row["position"]),
            catalogue_title=row["catalogue_title"],
            normalized_title=row["normalized_title"],
            status=row["status"],
            content=row["content"],
            last_error_type=row["last_error_type"],
            last_error=row["last_error"],
        )

    def save_completed_chapter(
        self,
        chapter_id: int,
        *,
        title: str,
        content: str,
        attempts: int = 1,
    ) -> None:
        """在同一事务中保存正文、哈希和完成状态。

        Args:
            chapter_id: 要更新的章节主键。
            title: 本次抓取后规范化的标题。
            content: 已通过完整性检查的正文。
            attempts: 本次运行实际尝试次数。

        Raises:
            ChapterNotFound: 章节不存在。

        内容哈希可用于判断正文是否改变。重要：正文、哈希、completed 状态和
        错误清理必须一起提交，不能拆成多个事务。
        """
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        connection = self._connection()
        with connection:
            cursor = connection.execute(
                """
                UPDATE chapters
                SET normalized_title = ?,
                    content = ?,
                    content_hash = ?,
                    status = 'completed',
                    attempt_count = attempt_count + ?,
                    last_error_type = NULL,
                    last_error = NULL,
                    last_attempt_at = CURRENT_TIMESTAMP,
                    fetched_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (title, content, content_hash, attempts, chapter_id),
            )
            if cursor.rowcount == 0:
                raise ChapterNotFound("章节 {} 不存在".format(chapter_id))

    def save_failed_chapter(
        self,
        chapter_id: int,
        *,
        error_type: str,
        error_message: str,
        attempts: int = 1,
        preserve_completed: bool = False,
    ) -> None:
        """记录最终失败；刷新场景可保留原有 completed 正文。

        Args:
            chapter_id: 失败章节主键。
            error_type: 适合程序判断的稳定错误类别。
            error_message: 适合用户阅读的详细错误信息。
            attempts: 本次运行实际尝试次数。
            preserve_completed: ``True`` 时，只要旧正文存在就保持 completed。

        Raises:
            ChapterNotFound: 章节不存在。

        重要：刷新失败不等于旧正文失效。``preserve_completed`` 使用 SQL
        CASE 表达式在同一次 UPDATE 中决定状态，避免先读后写造成不一致。
        """
        status_expression = (
            "CASE WHEN content IS NOT NULL THEN 'completed' ELSE 'failed' END"
            if preserve_completed
            else "'failed'"
        )
        connection = self._connection()
        with connection:
            cursor = connection.execute(
                """
                UPDATE chapters
                SET status = {},
                    attempt_count = attempt_count + ?,
                    last_error_type = ?,
                    last_error = ?,
                    last_attempt_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """.format(status_expression),
                (attempts, error_type, error_message, chapter_id),
            )
            if cursor.rowcount == 0:
                raise ChapterNotFound("章节 {} 不存在".format(chapter_id))

    def get_progress(
        self,
        source_id: int,
        *,
        start_position: Optional[int] = None,
    ) -> Progress:
        """统计整个来源或指定起始位置之后的章节进度。

        Args:
            source_id: 要统计的来源。
            start_position: 可选起点；更早章节不计入任何数字。

        Returns:
            ``Progress``，包含 total、completed、pending 和 failed。

        Raises:
            SourceNotFound: 来源不存在。

        SUM 在没有记录时会返回 NULL，因此构造结果时使用 ``or 0``。
        """
        self.get_source(source_id)
        parameters = [source_id]
        position_clause = ""
        if start_position is not None:
            position_clause = "AND position >= ?"
            parameters.append(start_position)
        row = self._connection().execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END)
                    AS completed,
                SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END)
                    AS pending,
                SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END)
                    AS failed
            FROM chapters
            WHERE source_id = ?
              {}
            """.format(position_clause),
            parameters,
        ).fetchone()
        return Progress(
            total=int(row["total"] or 0),
            completed=int(row["completed"] or 0),
            pending=int(row["pending"] or 0),
            failed=int(row["failed"] or 0),
        )

    def get_source_export_rows(
        self,
        source_id: int,
        *,
        start_position: Optional[int] = None,
    ) -> List[sqlite3.Row]:
        """返回按目录排序的导出数据，可限制本次导出的起始位置。

        重要：这里与 ``get_download_queue``、``get_progress`` 使用相同的
        ``start_position`` 语义，防止下载范围和导出范围不一致。
        """
        self.get_source(source_id)
        parameters = [source_id]
        position_clause = ""
        if start_position is not None:
            position_clause = "AND position >= ?"
            parameters.append(start_position)
        return self._connection().execute(
            """
            SELECT *
            FROM chapters
            WHERE source_id = ?
              {}
            ORDER BY position
            """.format(position_clause),
            parameters,
        ).fetchall()

    def create_merged_edition(
        self,
        name: str,
        *,
        description: Optional[str] = None,
        output_name: Optional[str] = None,
        control=None,
    ) -> int:
        """创建一个空合并版本。

        Args:
            name: 用户可见且唯一的合并版本名。
            description: 可选说明。
            output_name: 可选输出文件名；省略时使用 name。

        Returns:
            新合并版本的数据库主键。
        """
        connection = self._connection()
        unique_output_name = self._make_unique_output_name(
            output_name or name,
            table="merged_editions",
        )
        with connection:
            cursor = connection.execute(
                """
                INSERT INTO merged_editions (name, output_name, description)
                VALUES (?, ?, ?)
                """,
                (name, unique_output_name, description),
            )
            if control:
                control.reserve_commit()
            return int(cursor.lastrowid)

    def get_merged_edition(self, edition_id: int) -> MergedEdition:
        """按主键取得合并版本，不存在时抛出明确异常。"""
        row = self._connection().execute(
            "SELECT * FROM merged_editions WHERE id = ?",
            (edition_id,),
        ).fetchone()
        if row is None:
            raise MergeEditionNotFound(
                "合并版本 {} 不存在".format(edition_id)
            )
        return MergedEdition(
            id=int(row["id"]),
            name=row["name"],
            output_name=row["output_name"],
            description=row["description"],
        )

    def list_merged_editions(self) -> List[MergedEdition]:
        """返回所有合并版本，供 ``merge list`` 展示。"""
        rows = self._connection().execute(
            "SELECT * FROM merged_editions ORDER BY id"
        ).fetchall()
        return [
            MergedEdition(
                id=int(row["id"]),
                name=row["name"],
                output_name=row["output_name"],
                description=row["description"],
            )
            for row in rows
        ]

    def _next_merged_position(self, edition_id: int) -> int:
        """计算追加章节应使用的位置：当前最大位置加一。

        ``COALESCE`` 把空合并版的 MAX(NULL) 转为 0，所以第一章位置为 1。
        """
        row = self._connection().execute(
            """
            SELECT COALESCE(MAX(position), 0) + 1 AS next_position
            FROM merged_chapters
            WHERE edition_id = ?
            """,
            (edition_id,),
        ).fetchone()
        return int(row["next_position"])

    def append_source_range(
        self,
        edition_id: int,
        source_id: int,
        start_position: int,
        end_position: int,
    ) -> int:
        """把来源中的连续范围一对一追加到合并版本。

        Args:
            edition_id: 目标合并版本。
            source_id: 提供章节的来源。
            start_position: 来源范围起点（包含）。
            end_position: 来源范围终点（包含）。

        Returns:
            实际追加的合并章节数量。

        Raises:
            ChapterNotFound: 范围为空或中间缺少任一目录位置。

        “一对一”表示每个来源章节都生成一个 merged_chapter，并创建一个
        merged_chapter_part 引用它；正文没有被复制。
        """
        self.get_merged_edition(edition_id)
        self.get_source(source_id)
        rows = self._connection().execute(
            """
            SELECT *
            FROM chapters
            WHERE source_id = ?
              AND position BETWEEN ? AND ?
            ORDER BY position
            """,
            (source_id, start_position, end_position),
        ).fetchall()
        if not rows:
            raise ChapterNotFound(
                "来源 {} 在范围 {}-{} 内没有章节".format(
                    source_id,
                    start_position,
                    end_position,
                )
            )
        actual_positions = {int(row["position"]) for row in rows}
        # 不允许静默跳过范围中的缺章，否则合并版位置会与用户预期错位。
        expected_positions = set(range(start_position, end_position + 1))
        missing_positions = sorted(expected_positions - actual_positions)
        if missing_positions:
            raise ChapterNotFound(
                "来源 {} 缺少目录位置: {}".format(
                    source_id,
                    ", ".join(str(value) for value in missing_positions),
                )
            )

        connection = self._connection()
        next_position = self._next_merged_position(edition_id)
        with connection:
            for offset, row in enumerate(rows):
                title = row["normalized_title"] or row["catalogue_title"]
                cursor = connection.execute(
                    """
                    INSERT INTO merged_chapters (edition_id, position, title)
                    VALUES (?, ?, ?)
                    """,
                    (edition_id, next_position + offset, title),
                )
                # 单来源章节只有一个 part，因此 part_order 固定为 1，首段不
                # 需要前置分隔符。
                connection.execute(
                    """
                    INSERT INTO merged_chapter_parts (
                        merged_chapter_id,
                        source_chapter_id,
                        part_order,
                        separator
                    )
                    VALUES (?, ?, 1, '')
                    """,
                    (cursor.lastrowid, row["id"]),
                )
        return len(rows)

    def compose_merged_chapter(
        self,
        edition_id: int,
        *,
        title: Optional[str],
        source_positions: Sequence[Tuple[int, int]],
    ) -> int:
        """把多个来源章节按给定顺序组合成一个合并章节。

        Args:
            edition_id: 目标合并版本。
            title: 合并章节自定义标题，可为空。
            source_positions: ``(source_id, position)`` 序列，顺序即拼接顺序。

        Returns:
            新合并章节的数据库主键。

        Raises:
            ChapterNotFound: 未提供 part，或某个来源位置不能唯一匹配章节。

        数据库最终保存稳定的 chapter.id，而不是可能因目录调整而变化的
        position。position 仅用于 GUI 输入阶段查找章节。
        """
        self.get_merged_edition(edition_id)
        if not source_positions:
            raise ChapterNotFound("组合章节至少需要一个来源章节")

        connection = self._connection()
        source_chapter_ids = []
        for source_id, position in source_positions:
            # 要求恰好匹配一章，避免目录异常时错误选中任意一条记录。
            rows = connection.execute(
                """
                SELECT id
                FROM chapters
                WHERE source_id = ? AND position = ?
                ORDER BY id
                """,
                (source_id, position),
            ).fetchall()
            if len(rows) != 1:
                raise ChapterNotFound(
                    "来源 {} 的目录位置 {} 匹配到 {} 个章节".format(
                        source_id,
                        position,
                        len(rows),
                    )
                )
            source_chapter_ids.append(int(rows[0]["id"]))

        position = self._next_merged_position(edition_id)
        with connection:
            cursor = connection.execute(
                """
                INSERT INTO merged_chapters (edition_id, position, title)
                VALUES (?, ?, ?)
                """,
                (edition_id, position, title),
            )
            merged_chapter_id = int(cursor.lastrowid)
            for part_order, chapter_id in enumerate(
                source_chapter_ids,
                start=1,
            ):
                # 第一段直接开始，后续各段默认以两个换行符分隔。
                separator = "" if part_order == 1 else "\n\n"
                connection.execute(
                    """
                    INSERT INTO merged_chapter_parts (
                        merged_chapter_id,
                        source_chapter_id,
                        part_order,
                        separator
                    )
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        merged_chapter_id,
                        chapter_id,
                        part_order,
                        separator,
                    ),
                )
        return merged_chapter_id

    def get_merged_export_rows(self, edition_id: int) -> List[sqlite3.Row]:
        """取得导出合并版所需的扁平查询结果。

        Returns:
            每一行代表一个合并章节 part；同一 merged_chapter_id 可能出现
            多行。导出器随后按 merged_position 和 part_order 分组拼接。

        使用 LEFT JOIN 是为了即使某个合并章节尚无 part，也能导出明确占位，
        而不是让该合并章节从 TXT 中悄悄消失。
        """
        self.get_merged_edition(edition_id)
        return self._connection().execute(
            """
            SELECT
                mc.id AS merged_chapter_id,
                mc.position AS merged_position,
                mc.title AS merged_title,
                mcp.part_order,
                mcp.separator,
                c.id AS source_chapter_id,
                c.source_id,
                c.position AS source_position,
                c.catalogue_title,
                c.normalized_title,
                c.content,
                c.status,
                c.last_error_type,
                c.last_error
            FROM merged_chapters AS mc
            LEFT JOIN merged_chapter_parts AS mcp
                ON mcp.merged_chapter_id = mc.id
            LEFT JOIN chapters AS c
                ON c.id = mcp.source_chapter_id
            WHERE mc.edition_id = ?
            ORDER BY mc.position, mcp.part_order
            """,
            (edition_id,),
        ).fetchall()

    def list_source_summaries(self):
        """一次聚合读取书库进度，不在列表中传输正文。"""
        return [dict(row) for row in self._connection().execute(
            """SELECT s.*, COUNT(c.id) total,
            COALESCE(SUM(c.status='completed'),0) completed,
            COALESCE(SUM(c.status='pending'),0) pending,
            COALESCE(SUM(c.status='failed'),0) failed
            FROM sources s LEFT JOIN chapters c ON c.source_id=s.id
            GROUP BY s.id ORDER BY s.updated_at DESC, s.id DESC"""
        )]

    def source_detail(self, source_id):
        source = asdict(self.get_source(source_id))
        source['progress'] = asdict(self.get_progress(source_id))
        return source

    def chapter_page(self, source_id, page=1, page_size=50, status=None):
        """同一读取事务内取得总数和摘要；稳定排序避免同位置随机跳动。"""
        self.get_source(source_id)
        clause = 'source_id=?'
        args = [source_id]
        if status:
            clause += ' AND status=?'
            args.append(status)
        total = self._connection().execute(
            'SELECT COUNT(*) FROM chapters WHERE ' + clause, args
        ).fetchone()[0]
        rows = self._connection().execute(
            'SELECT id,source_id,position,catalogue_title,normalized_title,status,'
            'last_error_type,last_error FROM chapters WHERE ' + clause +
            ' ORDER BY position,id LIMIT ? OFFSET ?',
            args + [page_size, (page - 1) * page_size],
        )
        return {'items': [dict(row) for row in rows], 'total': total,
                'page': page, 'page_size': page_size}

    def chapter_preview(self, chapter_id):
        """正文与相邻章节身份在同一只读快照中返回，不预读相邻正文。"""
        chapter = self.get_chapter(chapter_id)
        result = asdict(chapter)
        for name, comparison, direction in (('previous_id', '<', 'DESC'), ('next_id', '>', 'ASC')):
            row = self._connection().execute(
                f'SELECT id FROM chapters WHERE source_id=? AND (position,id) {comparison} (?,?) '
                f'ORDER BY position {direction},id {direction} LIMIT 1',
                (chapter.source_id, chapter.position, chapter.id),
            ).fetchone()
            result[name] = row['id'] if row else None
        return result

    def chapter_ids(self, source_id, positions=None):
        """提交任务时将目录位置固化为身份，拒绝缺失或歧义位置。"""
        self.get_source(source_id)
        rows = list(self._connection().execute(
            'SELECT id,position FROM chapters WHERE source_id=? ORDER BY position,id',
            (source_id,),
        ))
        if positions is None:
            return [row['id'] for row in rows]
        mapping = {}
        for row in rows:
            mapping.setdefault(row['position'], []).append(row['id'])
        result = []
        for position in positions:
            matches = mapping.get(position, [])
            if len(matches) != 1:
                raise ChapterNotFound(f'目录位置 {position} 不存在或不唯一')
            result.append(matches[0])
        return result

    def chapters_by_ids(self, ids, source_id=None):
        """抓取与引用校验只取摘要；避免整本已完成正文进入任务内存。"""
        mapping = {}
        unique_ids = list(dict.fromkeys(ids))
        for offset in range(0, len(unique_ids), 900):
            batch = unique_ids[offset:offset+900]
            rows = self._connection().execute(
                'SELECT id,source_id,chapter_url,position,catalogue_title,normalized_title,'
                'status,NULL content,last_error_type,last_error FROM chapters WHERE id IN (' +
                ','.join('?' for _ in batch) + ')', batch,
            )
            mapping.update((row['id'], self._chapter_from_row(row)) for row in rows)
        if any(chapter_id not in mapping for chapter_id in ids):
            raise ChapterNotFound('选择的章节已不存在')
        chapters = [mapping[chapter_id] for chapter_id in ids]
        if source_id is not None and any(c.source_id != source_id for c in chapters):
            raise ChapterNotFound('选择的章节不属于该来源')
        return chapters

    def edition_page(self, edition_id, page=1, page_size=50):
        edition = asdict(self.get_merged_edition(edition_id))
        connection = self._connection()
        total = connection.execute(
            'SELECT COUNT(*) FROM merged_chapters WHERE edition_id=?', (edition_id,)
        ).fetchone()[0]
        items = []
        for row in connection.execute(
            'SELECT id,position,title FROM merged_chapters WHERE edition_id=? '
            'ORDER BY position,id LIMIT ? OFFSET ?',
            (edition_id, page_size, (page-1)*page_size),
        ):
            item = dict(row)
            item['parts'] = [dict(part) for part in connection.execute(
                'SELECT c.id,c.source_id,c.position,c.normalized_title,c.status '
                'FROM merged_chapter_parts p JOIN chapters c ON c.id=p.source_chapter_id '
                'WHERE p.merged_chapter_id=? ORDER BY p.part_order', (row['id'],)
            )]
            items.append(item)
        return {'edition': edition, 'items': items, 'total': total,
                'page': page, 'page_size': page_size}

    def merge_from_ids(self, edition_id, chapter_ids, *, compose=False, title=None, control=None):
        """按稳定 ID 原子追加；停止发生在提交许可前则整笔回滚。"""
        self.get_merged_edition(edition_id)
        chapters = self.chapters_by_ids(chapter_ids)
        if not chapters:
            raise ChapterNotFound('至少选择一个章节')
        connection = self._connection()
        with connection:
            position = self._next_merged_position(edition_id)
            groups = [chapters] if compose else [[chapter] for chapter in chapters]
            for offset, parts in enumerate(groups):
                if control:
                    control.check()
                chapter_title = title if compose else (parts[0].normalized_title or parts[0].catalogue_title)
                cursor = connection.execute(
                    'INSERT INTO merged_chapters(edition_id,position,title) VALUES (?,?,?)',
                    (edition_id, position+offset, chapter_title),
                )
                for order, chapter in enumerate(parts, 1):
                    if control:
                        control.check()
                    connection.execute(
                        'INSERT INTO merged_chapter_parts(merged_chapter_id,source_chapter_id,part_order,separator) '
                        'VALUES (?,?,?,?)', (cursor.lastrowid, chapter.id, order, '' if order == 1 else '\n\n'),
                    )
            if control:
                control.reserve_commit()
        return len(groups)

    def iter_source_export(self, source_id, start_position=None):
        self.get_source(source_id)
        return self._connection().execute(
            'SELECT * FROM chapters WHERE source_id=? AND position>=? ORDER BY position,id',
            (source_id, start_position or 1),
        )

    def iter_merged_export(self, edition_id):
        self.get_merged_edition(edition_id)
        return self._connection().execute(
            """SELECT mc.id merged_chapter_id, mc.position merged_position, mc.title merged_title,
            p.part_order,p.separator,c.id source_chapter_id,c.source_id,c.position source_position,
            c.catalogue_title,c.normalized_title,c.content,c.status,c.last_error_type,c.last_error
            FROM merged_chapters mc LEFT JOIN merged_chapter_parts p ON p.merged_chapter_id=mc.id
            LEFT JOIN chapters c ON c.id=p.source_chapter_id WHERE mc.edition_id=?
            ORDER BY mc.position,mc.id,p.part_order""", (edition_id,)
        )
