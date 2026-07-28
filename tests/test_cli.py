"""命令行参数解析测试。

这里直接调用纯函数和 ``ArgumentParser.parse_args``，不打开数据库、不启动
浏览器，也不需要真实运行一个终端进程。
"""

import unittest

from novelcrawl.cli import (
    build_parser,
    parse_position_spec,
    parse_source_position,
)
from novelcrawl.errors import InvalidChapterSpec


class CliTests(unittest.TestCase):
    """验证用户输入能被转换成业务层期望的数据。"""

    def test_position_spec_supports_ranges_and_individual_positions(self):
        """混合范围和单个位置应展开为有序列表。"""
        # assertEqual 会递归比较列表内容和顺序。
        self.assertEqual(
            parse_position_spec("1-3,5,8-9"),
            [1, 2, 3, 5, 8, 9],
        )

    def test_invalid_position_spec_is_rejected(self):
        """倒序范围必须抛出 InvalidChapterSpec。"""
        # assertRaises 是上下文管理器：代码块若抛出指定异常则测试通过；
        # 若没有异常或异常类型不同，测试失败。
        with self.assertRaises(InvalidChapterSpec):
            parse_position_spec("3-1")

    def test_source_position_format(self):
        """合并命令的 SOURCE:POSITION 应转换成整数元组。"""
        self.assertEqual(parse_source_position("2:101"), (2, 101))

    def test_no_arguments_do_not_select_an_interactive_command(self):
        """空参数不能隐式进入旧交互模式。"""
        # 传入 [] 等同于命令行没有任何子命令，但不会读取测试进程 sys.argv。
        args = build_parser().parse_args([])
        # assertIsNone 专门检查对象就是 None，而不是其他“假值”如 0 或 ""。
        self.assertIsNone(args.command)
