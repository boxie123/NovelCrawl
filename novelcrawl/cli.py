"""纯 argparse 命令入口；不带参数时只显示帮助，不进入交互模式。

CLI（Command-Line Interface，命令行界面）分成三个阶段：

    build_parser()
      声明有哪些命令、参数及帮助文本
        ↓
    argparse 解析用户输入，得到 Namespace
        ↓
    execute()
      创建 Storage/Service，并把命令分派到对应业务方法

这个文件只负责参数格式和用户输出，不包含网页解析或 SQL。
"""

import argparse
import asyncio
import sqlite3
import sys
from dataclasses import replace
from typing import Iterable, List, Optional, Sequence, Tuple

from novelcrawl.config import DEFAULT_CONFIG
from novelcrawl.errors import InvalidChapterSpec, NovelCrawlError
from novelcrawl.exporter import TxtExporter
from novelcrawl.logging_config import configure_logging
from novelcrawl.merge_service import MergeService
from novelcrawl.services import DownloadService
from novelcrawl.storage import Storage


def parse_position_spec(value: str) -> List[int]:
    """把 ``1-3,5`` 解析成有序且去重的目录位置。

    Args:
        value: 用户在 ``--chapters`` 后输入的范围表达式。

    Returns:
        正整数位置列表。例如 ``1-3,5`` 返回 ``[1, 2, 3, 5]``。

    Raises:
        InvalidChapterSpec: 表达式为空、包含非数字、倒序范围或非正数。

    先使用 set 去重，最终 sorted 保证结果按位置递增。
    """
    positions = set()
    if not value.strip():
        raise InvalidChapterSpec("章节范围不能为空")

    for segment in value.split(","):
        # 逗号分隔多个独立项目，每个项目可以是单值或闭区间。
        part = segment.strip()
        if not part:
            raise InvalidChapterSpec("章节范围中存在空项目")
        if "-" in part:
            # split(..., 1) 最多切一次；多余的连字符会使右侧不是纯数字，
            # 从而进入无效范围分支。
            pieces = part.split("-", 1)
            if len(pieces) != 2 or not all(piece.isdigit() for piece in pieces):
                raise InvalidChapterSpec("无效章节范围: {}".format(part))
            start, end = (int(piece) for piece in pieces)
            if start <= 0 or end < start:
                raise InvalidChapterSpec("无效章节范围: {}".format(part))
            positions.update(range(start, end + 1))
        else:
            if not part.isdigit() or int(part) <= 0:
                raise InvalidChapterSpec("无效章节序号: {}".format(part))
            positions.add(int(part))
    return sorted(positions)


def parse_source_position(value: str) -> Tuple[int, int]:
    """解析合并命令中的 ``来源ID:目录位置``。

    Args:
        value: 例如 ``2:101``。

    Returns:
        ``(2, 101)``，供合并服务查找稳定的 chapter.id。

    Raises:
        InvalidChapterSpec: 格式不正确或任一数字不是正整数。
    """
    pieces = value.split(":", 1)
    if (
        len(pieces) != 2
        or not pieces[0].isdigit()
        or not pieces[1].isdigit()
        or int(pieces[0]) <= 0
        or int(pieces[1]) <= 0
    ):
        raise InvalidChapterSpec(
            "来源章节必须使用 SOURCE_ID:POSITION 格式: {}".format(value)
        )
    return int(pieces[0]), int(pieces[1])


def parse_concurrency(value: str) -> int:
    """解析 CLI 并发数，并把允许范围限制为 1～5。"""
    try:
        concurrency = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("并发数必须是 1～5 的整数")
    if concurrency < 1 or concurrency > 5:
        raise argparse.ArgumentTypeError("并发数必须在 1～5 之间")
    return concurrency


def _add_concurrency_argument(parser: argparse.ArgumentParser) -> None:
    """为会抓取章节的子命令添加统一的本次运行并发选项。"""
    parser.add_argument(
        "--concurrency",
        type=parse_concurrency,
        default=None,
        metavar="N",
        help="本次任务每批并发章节数（1～5，默认 3）",
    )


def build_parser() -> argparse.ArgumentParser:
    """声明所有公开命令；业务实现统一委托给 service。

    Returns:
        配置完成的顶层 ``ArgumentParser``。

    ``subparsers`` 表示互斥的子命令，例如 download、resume 和 merge。
    ``dest="command"`` 让被选择的命令名出现在 ``args.command`` 中。
    """
    parser = argparse.ArgumentParser(
        prog="novelcrawl",
        description="支持 SQLite 断点续传的小说下载器",
    )
    subparsers = parser.add_subparsers(dest="command")

    # download 接受 URL；相同 URL 再次执行会同步目录并自动续传。
    download_parser = subparsers.add_parser(
        "download",
        help="添加来源或同步目录后自动续传",
    )
    download_parser.add_argument("url", help="小说目录 URL")
    download_parser.add_argument(
        "--start-position",
        type=int,
        help="仅下载该目录位置及之后的未完成章节",
    )
    _add_concurrency_argument(download_parser)

    resume_parser = subparsers.add_parser(
        "resume",
        help="同步已有来源目录并继续下载",
    )
    resume_parser.add_argument("source_id", type=int)
    resume_parser.add_argument("--start-position", type=int)
    _add_concurrency_argument(resume_parser)

    refresh_parser = subparsers.add_parser(
        "refresh",
        help="覆盖刷新指定来源章节",
    )
    refresh_parser.add_argument("source_id", type=int)
    # mutually_exclusive_group 确保 --chapters 和 --all 必须二选一，
    # argparse 会在业务代码运行前自动拒绝冲突或缺失输入。
    refresh_group = refresh_parser.add_mutually_exclusive_group(required=True)
    refresh_group.add_argument(
        "--chapters",
        help="目录位置，例如 1-10,15,20-25",
    )
    refresh_group.add_argument(
        "--all",
        action="store_true",
        help="刷新全部章节",
    )
    _add_concurrency_argument(refresh_parser)

    export_parser = subparsers.add_parser(
        "export",
        help="从数据库重新导出来源 TXT",
    )
    export_parser.add_argument("source_id", type=int)

    status_parser = subparsers.add_parser(
        "status",
        help="查看所有来源或指定来源进度",
    )
    status_parser.add_argument("source_id", type=int, nargs="?")

    # merge 自己还有第二层子命令，所以再创建一组 subparsers。
    merge_parser = subparsers.add_parser(
        "merge",
        help="管理跨来源合并版本",
    )
    merge_subparsers = merge_parser.add_subparsers(dest="merge_command")

    merge_create = merge_subparsers.add_parser(
        "create",
        help="创建合并版本",
    )
    merge_create.add_argument("name")
    merge_create.add_argument("--description")
    merge_create.add_argument("--output-name")

    merge_subparsers.add_parser("list", help="列出合并版本")

    merge_append = merge_subparsers.add_parser(
        "append-range",
        help="将来源章节范围一对一追加到合并版本",
    )
    merge_append.add_argument("edition_id", type=int)
    merge_append.add_argument("--source", type=int, required=True)
    merge_append.add_argument(
        "--chapters",
        required=True,
        help="连续范围，例如 1-100",
    )

    merge_compose = merge_subparsers.add_parser(
        "compose",
        help="将一个或多个来源章节组合为一个合并章节",
    )
    merge_compose.add_argument("edition_id", type=int)
    merge_compose.add_argument("--title")
    merge_compose.add_argument(
        "--part",
        action="append",
        required=True,
        help="来源 ID 与目录位置，例如 1:101；可重复",
    )
    # action="append" 允许多次写 --part，argparse 会把每个值收集进列表。

    merge_export = merge_subparsers.add_parser(
        "export",
        help="导出合并版本 TXT",
    )
    merge_export.add_argument("edition_id", type=int)
    return parser


def _print_status(storage: Storage, source_ids: Optional[Iterable[int]]) -> None:
    """打印全部来源或指定来源的进度表。

    Args:
        storage: 已打开的数据库访问对象。
        source_ids: 要显示的来源 ID；``None`` 表示全部。

    即使筛选结果为空，也会逐个调用 ``get_source`` 验证用户给出的 ID，
    使“不存在”和“数据库确实为空”得到不同反馈。
    """
    sources = storage.list_sources()
    if source_ids is not None:
        selected = set(source_ids)
        sources = [source for source in sources if source.id in selected]
        for source_id in selected:
            storage.get_source(source_id)

    if not sources:
        print("没有来源记录")
        return

    print("ID\t小说\t站点\t总数\t完成\t待下载\t失败")
    for source in sources:
        progress = storage.get_progress(source.id)
        print(
            "{id}\t{title}\t{site}\t{total}\t{completed}\t{pending}\t{failed}".format(
                id=source.id,
                title=source.title,
                site=source.site_name,
                total=progress.total,
                completed=progress.completed,
                pending=progress.pending,
                failed=progress.failed,
            )
        )


def _validate_positive(value: Optional[int], name: str) -> None:
    """验证可选整数参数为正数，``None`` 表示用户没有提供。"""
    if value is not None and value <= 0:
        raise InvalidChapterSpec("{} 必须大于 0".format(name))


def execute(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    """根据解析结果执行一个命令。

    Args:
        args: argparse 生成的命名空间，属性由所选子命令决定。
        parser: 顶层解析器，用于兜底显示帮助。

    Returns:
        进程退出码：``0`` 表示成功，``2`` 表示缺少有效命令。

    ``with Storage`` 保证整个命令共享一个数据库连接，并在命令结束或异常时
    自动关闭。服务对象共享同一个 Storage，避免各自维护连接和状态。
    """
    # 并发参数只覆盖当前进程中的批次大小，不写入数据库或配置文件。
    concurrency = getattr(args, "concurrency", None)
    config = (
        replace(DEFAULT_CONFIG, batch_size=concurrency)
        if concurrency is not None
        else DEFAULT_CONFIG
    )
    # delay=True 的日志 handler 只在真正出现 unknown 异常时创建日志文件。
    configure_logging(config.log_path)
    with Storage(config.database_path) as storage:
        # 依赖对象只在实际执行命令时创建；单纯 --help 不会创建数据库。
        exporter = TxtExporter(
            storage,
            config.source_export_dir,
            config.merged_export_dir,
        )
        download_service = DownloadService(storage, exporter, config)
        merge_service = MergeService(storage, exporter)

        if args.command == "download":
            # 网络业务是 async 函数；asyncio.run 创建事件循环并等待其完成。
            _validate_positive(args.start_position, "--start-position")
            asyncio.run(
                download_service.download_url(
                    args.url,
                    start_position=args.start_position,
                )
            )
        elif args.command == "resume":
            _validate_positive(args.source_id, "source_id")
            _validate_positive(args.start_position, "--start-position")
            asyncio.run(
                download_service.resume_source(
                    args.source_id,
                    start_position=args.start_position,
                )
            )
        elif args.command == "refresh":
            _validate_positive(args.source_id, "source_id")
            positions = (
                # --all 转成 None，这是服务层约定的“全部章节”含义。
                None if args.all else parse_position_spec(args.chapters)
            )
            asyncio.run(
                download_service.refresh_source(
                    args.source_id,
                    positions=positions,
                )
            )
        elif args.command == "export":
            _validate_positive(args.source_id, "source_id")
            path = exporter.export_source(args.source_id)
            print("已导出: {}".format(path))
        elif args.command == "status":
            source_ids = None if args.source_id is None else [args.source_id]
            _print_status(storage, source_ids)
        elif args.command == "merge":
            if args.merge_command == "create":
                edition_id = merge_service.create(
                    args.name,
                    description=args.description,
                    output_name=args.output_name,
                )
                print("已创建合并版本，ID: {}".format(edition_id))
            elif args.merge_command == "list":
                editions = storage.list_merged_editions()
                if not editions:
                    print("没有合并版本")
                for edition in editions:
                    print(
                        "{}\t{}\t{}".format(
                            edition.id,
                            edition.name,
                            edition.output_name,
                        )
                    )
            elif args.merge_command == "append-range":
                positions = parse_position_spec(args.chapters)
                # append-range 的业务语义是连续范围；虽然通用解析器接受
                # “1-3,5”，这里会额外拒绝不连续输入。
                if positions != list(range(positions[0], positions[-1] + 1)):
                    raise InvalidChapterSpec(
                        "append-range 只接受一个连续章节范围"
                    )
                count = merge_service.append_range(
                    args.edition_id,
                    args.source,
                    positions[0],
                    positions[-1],
                )
                print("已追加 {} 个章节".format(count))
            elif args.merge_command == "compose":
                # 每个 --part 字符串先转换成 (source_id, position)。
                parts = [
                    parse_source_position(value)
                    for value in args.part
                ]
                merged_chapter_id = merge_service.compose(
                    args.edition_id,
                    title=args.title,
                    source_positions=parts,
                )
                print(
                    "已创建组合章节，ID: {}".format(merged_chapter_id)
                )
            elif args.merge_command == "export":
                path = merge_service.export(args.edition_id)
                print("已导出: {}".format(path))
            else:
                parser.print_help()
                return 2
        else:
            parser.print_help()
            return 2
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI 顶层入口，负责解析参数和统一转换异常为退出码。

    Args:
        argv: 可选参数序列。``None`` 时 argparse 自动读取 ``sys.argv``；
            测试可传入列表而不启动真实子进程。

    Returns:
        ``0`` 成功，``1`` 预期业务/数据库错误，``2`` 缺少子命令，
        ``130`` 用户按 Ctrl+C 取消。

    这里不调用 ``sys.exit``，便于测试直接检查返回值。根目录 ``main.py``
    再通过 ``raise SystemExit(main())`` 把返回值交给操作系统。
    """
    parser = build_parser()
    # parse_args 负责类型转换、必填检查和自动生成 --help。
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 2
    if args.command == "merge" and args.merge_command is None:
        parser.print_help()
        return 2
    try:
        return execute(args, parser)
    except (NovelCrawlError, ValueError, sqlite3.Error) as exc:
        # 预期错误只输出简洁信息，不向普通用户展示完整 traceback。
        print("错误: {}".format(exc), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("已取消", file=sys.stderr)
        return 130
