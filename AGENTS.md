# AGENTS.md

本文件面向在此仓库中工作的编码代理。NovelCrawl 使用 Playwright + BeautifulSoup 抓取内容，SQLite 保存正文和进度，TXT 仅作为导出结果。

## 开始工作前

- 先阅读 `README.md`，确认命令行为和法律声明。
- 执行 `git status --short`，保留用户已有改动，不要擅自重置工作树。
- 优先使用 Python 标准库；引入新依赖前说明必要性。
- 不要在测试中访问真实小说网站，也不要提交数据库、导出 TXT 或下载正文。

## 结构

```text
main.py                    CLI 入口
novelcrawl/cli.py          argparse 命令
novelcrawl/services.py     下载与刷新编排
novelcrawl/crawler.py      浏览器访问、目录和正文翻页
novelcrawl/parsers.py      HTML 解析及标题规范化
novelcrawl/storage.py      SQLite schema 和事务
novelcrawl/exporter.py     来源/合并版 TXT 原子导出
novelcrawl/merge_service.py 合并业务
novelcrawl/site_config.py  站点选择器
tests/                     无网络单元测试
```

## 关键约束

- SQLite 是正文与下载状态的唯一数据源。
- Playwright 任务并发抓取，但数据库由主协程串行写入。
- 一个成功章节的正文与 `completed` 状态必须在同一事务提交。
- 普通续传只处理 `pending`、`failed`；刷新可处理任意章节。
- 刷新失败时，已有 `completed` 正文和状态不得被清除。
- 目录同步按 `(source_id, chapter_url)` 去重，不重置完成状态。
- `--start-position` 同时限制本次进度统计、下载队列和来源 TXT 导出范围。
- 一次下载或刷新任务结束后只导出一次 TXT，不得恢复为每批导出。
- TXT 必须先写同目录临时文件，再用 `os.replace()` 原子替换。
- 合并版本只引用 `chapters.id`，不得复制或覆盖来源正文。
- `books/` 是旧版文件目录，新代码不得自动修改。
- 单章异常必须转换为失败结果，不得取消同批其他章节。
- 不带参数时 CLI 只显示帮助，不得恢复交互模式。

## 数据库

共享数据库位于 `data/novelcrawl.db`。

- 第一版固定使用 `PRAGMA user_version = 1`。
- 当前不实现自动迁移；不支持的版本应直接报错。
- schema 或状态语义变化时必须补充存储层测试。
- 运行时数据库及其 WAL/SHM 文件必须保持在 `.gitignore` 中。

## 编码约定

- 目标版本为 Python 3.9+，不要使用仅在更高版本可用的语法。
- 模块职责保持单一：爬虫不写数据库，存储层不抓网页，导出层不修改下载状态。
- 公共方法和不直观的事务、重试、原子写入逻辑使用简短中文注释或 docstring。
- URL 作为来源和章节身份时必须先规范化；不要用易变化的章节标题去重。
- 文件名必须经过安全处理，路径使用 `pathlib.Path`。
- 不要自动删除、导入或覆盖 `books/` 中的旧文件。

## 测试

```powershell
python -m unittest discover -v
```

提交实现前至少执行：

```powershell
python -m unittest discover -v
python -m compileall -q main.py novelcrawl tests
git diff --check
```

测试使用临时 SQLite 数据库，不得访问真实网站。修改以下行为时必须增加回归测试：

- 目录同步和章节去重
- `start-position` 的统计、队列与导出范围
- 失败重试及刷新失败保留正文
- TXT 原子导出和失败占位
- 跨来源合并引用
- CLI 参数解析

## 合规与安全

- 保留 `README.md` 中的法律声明、免责声明和请求频率提示。
- 默认并发数和等待时间属于降低目标站点压力的保护设置，不应无理由提高。
- 真实抓取只能由用户明确发起；常规验证使用 mock、静态 HTML 和临时数据库。
