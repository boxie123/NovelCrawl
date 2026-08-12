# NovelCrawl

基于 Playwright、BeautifulSoup 和 SQLite 的异步小说下载器。SQLite 保存完整章节正文并作为唯一数据源，TXT 是可重复生成的阅读文件。

## 功能

- 按章节持久化，程序中断后自动续传
- 新章节优先、失败章节自动重试
- 手动刷新指定章节或全部章节
- 刷新成功直接覆盖正文，刷新失败保留已有正文
- TXT 按目录顺序导出，并为失败/未下载章节生成占位
- 不同网站来源独立管理
- 支持把不同来源的指定章节组合为独立合并版本
- 并发抓取、串行事务写库

## 项目数据

```text
data/novelcrawl.db       SQLite 数据库（运行时创建）
exports/sources/         单来源 TXT
exports/merged/          合并版本 TXT
books/                   旧版 TXT；新版本不会读取或覆盖
```

## 安装

项目要求 Python 3.9 或更高版本。

```powershell
uv sync
uv run playwright install chromium
```

也可以使用其他 Python 环境安装 `pyproject.toml` 中的依赖。

## 命令

查看帮助：

```powershell
uv run python main.py --help
```

添加来源或同步目录后自动续传：

```powershell
uv run python main.py download "https://www.sudugu.org/1811/"
```

从指定目录位置开始本次下载：

```powershell
uv run python main.py download "https://www.sudugu.org/1811/" --start-position 500
```

临时调整本次任务的章节并发数（允许 1～5，默认 3）：

```powershell
uv run python main.py download "https://www.sudugu.org/1811/" --concurrency 2
uv run python main.py resume 1 --concurrency 1
```

并发数同时决定每批处理的章节数；每批之间仍按默认设置等待 10 秒。提高
并发数会提高整体请求速率，应根据目标网站规则和响应情况谨慎使用。

指定 `--start-position` 后，本次进度统计、待下载队列和 TXT 导出均从该目录位置开始，不包含更早的章节。未指定时处理和导出整个来源。

按来源 ID 续传：

```powershell
uv run python main.py resume 1
```

查看进度：

```powershell
uv run python main.py status
uv run python main.py status 1
```

刷新指定章节：

```powershell
uv run python main.py refresh 1 --chapters "100-120,125"
uv run python main.py refresh 1 --all
uv run python main.py refresh 1 --chapters "100-120" --concurrency 1
```

刷新会先预访问一次来源目录页，在同一浏览器会话中建立 Cookie；章节第一页
使用目录 URL 作为 Referer，正文后续分页使用上一页 URL 作为 Referer。目录
预访问失败时会停止刷新，不继续产生一批无效章节请求。

重新导出 TXT：

```powershell
uv run python main.py export 1
```

### 合并版本

创建：

```powershell
uv run python main.py merge create "狂徒！合并版"
```

把来源 1 的第 1～100 个目录章节一对一追加到合并版本 1：

```powershell
uv run python main.py merge append-range 1 --source 1 --chapters "1-100"
```

将多个来源章节组合为一个合并章节：

```powershell
uv run python main.py merge compose 1 `
  --title "第一百零一章 完整版" `
  --part 1:101 `
  --part 2:101 `
  --part 2:102
```

导出：

```powershell
uv run python main.py merge export 1
```

合并版本只保存来源章节引用。来源正文刷新后，下次导出会自动使用最新正文。

## 数据安全语义

- 正文、哈希和完成状态在同一个 SQLite 事务中提交。
- 普通续传不会重新请求已完成章节。
- 单章失败不会取消同批其他成功章节。
- 一次下载或刷新任务结束后只导出一次 TXT，不会每批重复导出。
- TXT 先写入临时文件，完成后再原子替换正式文件。
- 第一版数据库版本固定为 `1`；遇到其他版本会停止，不自动迁移。
- 旧 `books/` 目录中的 TXT 不会被自动导入、删除或覆盖。

## 测试

```powershell
uv run python -m unittest discover -v
```

测试使用临时数据库，不访问真实网站。

## 法律声明

本项目仅供个人学习和技术研究，严禁用于任何商业用途或大规模批量抓取。使用者须自行判断其使用行为是否合法，并遵守以下准则。

### 使用者义务

1. **尊重版权** — 仅抓取已获得合法授权或处于公共领域的作品。不得将抓取内容用于二次分发、公开传播或商业行为。
2. **遵守 robots.txt 与服务条款** — 使用前应主动检查目标网站的 `robots.txt`、服务条款和访问规则。本项目不会替使用者自动确认抓取授权。
3. **保持合理请求频率** — 项目默认每批最多并发请求 3 章，批次间等待 10 秒。不得以修改配置等方式对目标服务发起高频或破坏性请求。
4. **限于个人使用** — 下载内容仅供个人离线阅读，不得大量下载后公开传播、共享或建立镜像服务。
5. **遵守适用法律** — 使用者应遵守所在国家或地区关于网络数据采集、版权及个人信息保护的法律法规，包括但不限于《中华人民共和国网络安全法》《中华人民共和国著作权法》《中华人民共和国个人信息保护法》及欧盟 GDPR 等。

### 免责声明

- 本项目按“现状”提供，不保证目标网站始终可访问、解析结果完整或下载内容准确。
- 本项目作者及贡献者不对使用者的违规抓取、侵权传播、绕过访问控制或其他违法违规行为承担责任。
- 使用者因安装、运行或修改本项目产生的法律纠纷、版权侵权、数据损失、账号限制或其他后果，均由使用者自行承担。
- 项目仓库不附带任何小说正文。程序仅按使用者明确提供的来源地址，将抓取结果保存在使用者本地 SQLite 数据库和导出文件中。
- 断点续传、刷新和合并功能用于减少重复请求及管理个人本地数据，不代表使用者获得了抓取、复制或传播目标内容的授权。

### 技术防护措施

项目保留以下默认限制，以降低误用及对目标服务器造成压力的风险：

- **并发限制** — 默认每批最多 3 章并发请求。
- **可控并发** — 网络命令可用 `--concurrency` 临时设置 1～5；提高并发会增加整体请求速率。
- **请求间隔** — 默认批次间等待 10 秒。
- **有限重试** — 页面导航和章节任务仅进行有限次数重试，持续失败会记录原因并停止本次尝试。
- **避免重复请求** — 普通续传跳过已完成章节，减少对相同内容的重复访问。
- **失败可见** — 下载失败会写入数据库并在 TXT 中生成占位，不会通过无限重试尝试绕过限制。

如果目标服务器返回拒绝访问、风控提示或持续错误，请立即停止抓取，不要尝试绕过验证码、访问控制、反爬措施或其他技术限制。

## 许可证

本项目基于 [Apache License 2.0](LICENSE) 发布。Apache-2.0 包含软件按“现状”提供的免责声明和责任限制，且不授予使用项目名称或商标掩盖侵权行为的权利。请阅读完整许可证文本。

## 免责协议

**使用本项目代码即表示您已阅读并同意上述条款。若不同意以上任何条款，请勿安装、运行或使用本项目。**
