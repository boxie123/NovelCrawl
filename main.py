"""兼容入口：把仓库根目录运行方式转交给包内 CLI。"""

from novelcrawl.cli import main


if __name__ == "__main__":
    # SystemExit 把 main() 返回的整数转换为操作系统可见的进程退出码。
    raise SystemExit(main())
