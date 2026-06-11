# NovelCrawl

基于 Playwright + BeautifulSoup 的异步小说爬虫，用于从多个中文小说网站爬取章节内容并保存为本地 TXT 文件。

## 项目结构

```
main.py          — 主入口：浏览器管理、目录爬取、章节内容获取与写入
utils.py         — 工具函数：中文数字转换(chineseNumber2Int)、文件写入(txt_write)、文件清理(remove_title)
page_config.py   — 站点配置：各站点的 CSS 选择器、循环参数、域名匹配(match_url)
test_novel_connent.py — 单章内容提取测试脚本
books/           — 爬取结果输出目录，格式: <小说名>_<站点>.txt
```

## 核心流程

1. 用户输入小说目录页 URL
2. `page_config.match_url()` 按域名匹配预设的 CSS 选择器，未匹配则手动输入
3. 启动 Playwright 无头 Chromium（UA 伪装为 Chrome 114）
4. `get_catalogue_page_content()` 爬取目录页（支持翻页），获取所有章节链接
5. `get_catalogue_url_list()` 用 BeautifulSoup 解析章节标题和 URL
6. 批次并发爬取：每批 `PRE_LOOP_NUM` 章并发，批次间等待 `PRE_LOOP_WAIT_TIME` 秒
7. `get_novel_content()` 解析正文、统一章节号格式、写入 TXT

## 关键设计

- **反爬策略**：Playwright 无头浏览器绕过 JS 检测；请求失败自动重试 5 次
- **章节号统一**：中文数字（一二三/壹贰叁）→ 阿拉伯数字；简写法（一三三）也支持
- **翻页处理**：目录页和正文页都支持"下一页"翻页爬取
- **并发控制**：`TaskGroup` 批量并发 + 批次间 sleep，避免压垮目标服务器
- **风控适配**：`test.html` 是风控页面样本，用于调试被拦截的情况

## 依赖

- Python >= 3.9
- `playwright` — 浏览器自动化，绕过反爬
- `beautifulsoup4` + `lxml` — HTML 解析

## 运行

```bash
python main.py
# 按提示输入目录 URL 和起始章节号，回车使用默认值
```

## 添加新站点

在 `page_config.py` 的 `PAGE_CONFIG` 列表中添加字典，提供域名和 5 个 CSS 选择器：
- `catalogue_selector` — 章节列表项
- `catalogue_btn` — 目录翻页按钮（无可留空）
- `catalogue_title` — 小说标题
- `novel_selector` — 正文容器
- `novel_btn` — 正文翻页按钮（无可留空）
