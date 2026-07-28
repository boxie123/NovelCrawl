"""项目日志配置。

下载失败等预期问题保存在 SQLite；只有可能代表代码缺陷的 unknown 异常才
写完整 traceback。日志轮转可防止长期运行后单个文件无限增长。
"""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


LOGGER_NAME = "novelcrawl"


def configure_logging(log_path: Path) -> None:
    """为 NovelCrawl 日志器添加一个延迟创建的轮转文件处理器。

    Args:
        log_path: 主日志文件路径。

    重复调用不会添加重复 handler。``delay=True`` 表示只有真正发生 unknown
    异常时才创建文件，执行 status、export 等正常命令不会产生空日志。
    """
    logger = logging.getLogger(LOGGER_NAME)
    if any(
        getattr(handler, "_novelcrawl_file_handler", False)
        for handler in logger.handlers
    ):
        return

    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        str(log_path),
        maxBytes=1_000_000,
        backupCount=3,
        encoding="utf-8",
        delay=True,
    )
    handler._novelcrawl_file_handler = True
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s\n%(message)s"
        )
    )
    logger.addHandler(handler)
    logger.setLevel(logging.ERROR)
    logger.propagate = False
