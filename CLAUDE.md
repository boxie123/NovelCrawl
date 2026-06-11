# NovelCrawl

基于 Playwright + BeautifulSoup 的异步小说爬虫，用于从多个中文小说网站爬取章节内容并保存为本地 TXT 文件。

## 项目结构

```
main.py          — 主入口：浏览器管理、目录爬取、章节内容获取与写入
utils.py         — 工具函数：中文数字转换、文件写入、文件清理
page_config.py   — 站点配置：各站点的 CSS 选择器、循环参数、域名匹配
books/           — 爬取结果输出目录，格式: <小说名>_<站点>.txt
```

## 运行逻辑

### 启动阶段（`__main__` 块，main.py:227）

1. **收集参数** — 交互式输入目录 URL（默认 bequke.com）、起始章节号
2. **提取站点名** — 从 URL 域名中解析站点标识，拼入输出文件名 `小说名_站点.txt`
3. **匹配选择器** — `page_config.match_url()` 按域名查找预设 CSS 选择器，未匹配则手动输入 5 个选择器
4. **启动异步主循环** — `asyncio.run(main())`

### 主协程（`main()`，main.py:185）

1. **启动浏览器** — Playwright 无头 Chromium，UA 伪装 Chrome 114，默认超时 300s
2. **爬取目录** — `get_catalogue_page_content()` 递归获取目录页（支持翻页）
3. **解析目录** — `get_catalogue_url_list()` 用 BeautifulSoup 提取章节目录和 URL
4. **清理旧文件** — `remove_title()` 删除已存在的同名 TXT
5. **批次并发爬取** — 每批 `PRE_LOOP_NUM`（3）章并发，批次间等待 `PRE_LOOP_WAIT_TIME`（10s）
   - `get_novel_page_content()` 获取单章所有页面内容（支持正文翻页）
   - `get_novel_content()` 解析正文、统一章节号、写入 TXT
6. **资源清理** — `try/finally` 确保 browser 和 playwright 一定关闭

### 目录爬取（`get_catalogue_page_content()`，main.py:151）

- 递归函数，首次调用创建新 Page，后续翻页复用同一个 Page
- 通过 `catalogue_btn_selector` 查找翻页按钮，遇到 `javascript:void(0)` 停止
- 新 Page 创建后自动注入 `playwright-stealth` 反检测脚本

### 正文爬取（`get_novel_page_content()`，main.py:109）

- 递归函数，首次调用创建新 Page，正文多页时复用同一个 Page
- 通过 `novel_btn_selector` 查找"下一页"按钮进行翻页
- 返回 `content_list`（每页的 HTML 和标题），供 `get_novel_content()` 解析

### 正文解析（`get_novel_content()`，main.py:51）

- 遍历多页 HTML，第一页提取并规范化章节标题
- 章节号规范化：中文数字（一二三/壹贰叁）→ 阿拉伯数字；移除"正文卷"前缀
- 提取正文时移除内嵌 `<div>`（可能是广告或无关内容）
- 跳过逻辑：正文为空 → 警告跳过；正文 < 400 字符且匹配风控提示语 → 跳过
- 返回 `bool`，调用方据此统计每批成功/跳过数量

### 章节号规范化规则

- 正则匹配 `第?N[章张]?` 格式（N 可为阿拉伯/中文/繁写数字）
- 中文简写（如"一三三" = 133）直接拼接各位数字
- 中文标准写法（如"一百二十三"）按位权计算

## 关键设计

- **反爬策略**：Playwright 无头浏览器 + `playwright-stealth` 反检测 + UA 伪装；失败自动重试 5 次
- **翻页处理**：目录页和正文页均支持翻页，递归实现，遇终止条件自动停止
- **并发控制**：`asyncio.TaskGroup` 批量并发 + 批次间 sleep，避免压垮目标服务器
- **错误隔离**：顶层 `try/except/finally` 确保异常时资源释放，异常信息打印后重新抛出

## 依赖

- Python >= 3.9
- `playwright` — 浏览器自动化
- `playwright-stealth` — 反检测浏览器指纹
- `beautifulsoup4` + `lxml` — HTML 解析

## 运行

```bash
python main.py
# 按提示输入目录 URL 和起始章节号，回车使用默认值
```

## 添加新站点

在 `page_config.py` 的 `PAGE_CONFIG` 列表中添加字典，提供域名和 5 个 CSS 选择器：

| 字段 | 说明 |
|------|------|
| `domain` | 域名列表，如 `["www.example.com"]` |
| `catalogue_selector` | 章节列表项选择器 |
| `catalogue_btn` | 目录翻页按钮（无可留空 `""`） |
| `catalogue_title` | 小说标题选择器 |
| `novel_selector` | 正文容器选择器 |
| `novel_btn` | 正文翻页按钮（无可留空 `""`） |
