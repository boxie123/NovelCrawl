"""NovelCrawl 包元数据。"""

import logging


# 库代码默认不自行配置输出目标。NullHandler 避免在测试或被其他程序导入时
# 把错误日志意外打印到 stderr；CLI 启动后会换成真正的文件处理器。
logging.getLogger(__name__).addHandler(logging.NullHandler())

# 代码版本与 pyproject.toml 中的项目版本保持一致。
__version__ = "0.2.0"
