"""模块之间传递的数据对象，避免依赖结构不明确的裸字典。

``@dataclass`` 会根据字段自动生成初始化函数和便于调试的字符串表示。
``frozen=True`` 表示对象创建后字段不可修改，可避免一个模块无意中改变另一个
模块正在使用的数据。数据模型同时供存储层、抓取层和 API 使用。
"""

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class SiteConfig:
    """一个网站的解析规则。

    Attributes:
        key: 数据库持久化使用的稳定配置键。
        domains: 此规则可匹配的域名元组。
        catalogue_selector: 目录中单个章节条目的 CSS 选择器。
        catalogue_button_selector: 目录翻页按钮；空字符串表示不分页。
        catalogue_title_selector: 小说名的 CSS 选择器。
        novel_selector: 正文容器的 CSS 选择器。
        novel_button_selector: 正文翻页按钮；空字符串表示不分页。
    """

    key: str
    domains: tuple
    catalogue_selector: str
    catalogue_button_selector: str
    catalogue_title_selector: str
    novel_selector: str
    novel_button_selector: str


@dataclass(frozen=True)
class CatalogueChapter:
    """从网站目录解析出的、尚未写入数据库的章节。"""

    url: str
    title: str
    normalized_title: str
    position: int


@dataclass(frozen=True)
class SourceRecord:
    """数据库中的一个独立网站来源。"""

    id: int
    catalogue_url: str
    title: str
    site_name: str
    site_config_key: str
    output_name: str


@dataclass(frozen=True)
class ChapterRecord:
    """数据库章节在抓取和刷新流程中的只读表示。

    ``status`` 可能为 pending、completed 或 failed；``content`` 只有成功
    章节通常才有值，错误字段则用于失败占位和排查。
    """

    id: int
    source_id: int
    url: str
    position: int
    catalogue_title: Optional[str]
    normalized_title: Optional[str]
    status: str
    content: Optional[str]
    last_error_type: Optional[str]
    last_error: Optional[str]


@dataclass(frozen=True)
class FetchResult:
    """一次章节抓取的统一结果，不用异常区分常规成功或失败。"""

    chapter_id: int
    title: str
    content: Optional[str]
    error_type: Optional[str] = None
    error_message: Optional[str] = None

    @property
    def succeeded(self) -> bool:
        """仅在没有错误类型且正文非空时视为成功。"""
        return self.error_type is None and bool(self.content)


@dataclass(frozen=True)
class Progress:
    """某个来源或某个 start-position 范围内的状态计数。"""

    total: int
    completed: int
    pending: int
    failed: int


@dataclass(frozen=True)
class MergedEdition:
    """用户创建的合并版本基本信息。"""

    id: int
    name: str
    output_name: str
    description: Optional[str]
