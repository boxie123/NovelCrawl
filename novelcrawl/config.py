"""应用级路径和运行参数。

配置集中在这里，业务模块通过 ``AppConfig`` 接收值，而不是散落多个魔法数字。
测试可构造自己的 AppConfig，把数据库和导出路径指向临时目录。
"""

from dataclasses import dataclass
from pathlib import Path


# __file__ 是当前 config.py；两次 parent 得到仓库根目录。
PACKAGE_ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT = PACKAGE_ROOT if (PACKAGE_ROOT / 'pyproject.toml').is_file() else Path.cwd()


@dataclass(frozen=True)
class AppConfig:
    """运行配置及默认值。

    Attributes:
        database_path: 共享 SQLite 文件。
        source_export_dir: 普通来源 TXT 目录。
        merged_export_dir: 合并版本 TXT 目录。
        log_path: 未预期异常的本地堆栈日志。
        batch_size: 同时抓取的最大章节数量；GUI 可为本次任务临时覆盖。
        batch_wait_seconds: 相邻批次之间的等待秒数。
        max_attempts_per_run: 单章在一次运行中的最大尝试次数。
        navigation_timeout_ms: Playwright 操作默认超时，单位毫秒。
        navigation_retries: 单个页面导航的最大尝试次数。
    """

    database_path: Path = PROJECT_ROOT / "data" / "novelcrawl.db"
    source_export_dir: Path = PROJECT_ROOT / "exports" / "sources"
    merged_export_dir: Path = PROJECT_ROOT / "exports" / "merged"
    log_path: Path = PROJECT_ROOT / "logs" / "novelcrawl.log"
    batch_size: int = 3
    batch_wait_seconds: float = 10.0
    max_attempts_per_run: int = 3
    navigation_timeout_ms: int = 300_000
    navigation_retries: int = 5


# GUI 使用默认实例；测试传入临时路径，不修改真实数据库。
DEFAULT_CONFIG = AppConfig()
