"""GUI 入口：启动本地服务并打开默认浏览器。"""

from novelcrawl.gui import main


if __name__ == "__main__":
    # SystemExit 把 main() 返回的整数转换为操作系统可见的进程退出码。
    raise SystemExit(main())
