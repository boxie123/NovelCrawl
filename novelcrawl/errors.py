"""项目可预期异常。

这些异常代表用户输入、站点解析或数据库版本等可说明的问题。GUI 会捕获
``NovelCrawlError`` 并打印简洁提示，而不是向普通用户输出完整 traceback。
"""


class NovelCrawlError(Exception):
    """所有可预期应用异常的基类。"""


class UnsupportedDatabaseVersion(NovelCrawlError):
    """数据库 user_version 与当前代码不兼容。"""

    pass


class SiteConfigNotFound(NovelCrawlError):
    """URL 域名或数据库配置键没有对应站点规则。"""

    pass


class CatalogueParseError(NovelCrawlError):
    """目录为空，或 CSS 选择器无法提取小说/章节。"""

    pass


class SourceNotFound(NovelCrawlError):
    """用户指定的来源 ID 不存在。"""

    pass


class ChapterNotFound(NovelCrawlError):
    """章节 ID、目录位置或合并范围无法匹配。"""

    pass


class MergeEditionNotFound(NovelCrawlError):
    """用户指定的合并版本 ID 不存在。"""

    pass


class InvalidChapterSpec(NovelCrawlError):
    """GUI 章节范围表达式格式或取值无效。"""

    pass


class PageNavigationError(NovelCrawlError):
    """页面持续无响应或返回非成功 HTTP 状态。"""

    pass
