# Yuii Reader

Yuii Reader 是一个面向 TOS 7 的原生 RSS / Atom 阅读器。它把订阅、分类、已读状态、星标、搜索和 OPML 迁移放在一个本地运行的 iframe 应用里，不依赖外部账号，也不把阅读数据上传到第三方。

- 作者与发布者：**Yuii**
- 当前版本：**0.1.0**
- 许可证：**MIT**
- 仓库：<https://github.com/642671/yuii-reader-tos7>

## 设计来源

Yuii Reader 不是对某个项目的代码复刻，而是在产品形态和交互上参考了两个成熟的开源 RSS 阅读器：

- [Miniflux](https://github.com/miniflux/v2)：极简的三栏阅读布局、已读/未读模型、键盘操作和自托管优先的理念。
- [FreshRSS](https://github.com/FreshRSS/FreshRSS)：订阅分类、OPML 导入导出、刷新策略和长期维护的社区型产品结构。

实现全部使用 Python 标准库和原生 HTML/CSS/JavaScript 重写，面向 TOS 7 的 iframe 应用规范，没有引入 Miniflux 或 FreshRSS 的源码。两者的许可证（Apache-2.0 / AGPL-3.0）仅作为设计参考，不构成代码授权关系。

## 功能

### 订阅与抓取

- 支持 RSS 2.0、RSS 1.0（RDF）和 Atom 1.0。
- 粘贴站点首页时自动发现订阅源（`<link rel="alternate">`）。
- 识别 UTF-8、GBK、GB18030、Big5 等常见编码，兼容 BOM 和 XML 声明。
- 使用 ETag / Last-Modified 条件请求，服务器返回 304 时不重复解析。
- 支持 gzip / deflate 压缩响应，限制重定向次数、单次抓取体积和超时时间。
- 订阅可按分类管理，支持重命名、删除和启用/停用。
- OPML 2.0 导入导出，便于从其他阅读器迁移。

### 阅读体验

- 三栏布局：订阅树、文章列表、正文阅读区。
- 未读、全部、星标三种全局视图；按订阅或分类筛选。
- 服务端清洗正文 HTML：白名单标签、移除脚本/事件属性/iframe、去除跟踪参数和 1×1 跟踪像素。
- 图片经本地代理加载（`api/media`），避免混合内容和第三方 Referer 泄漏。
- 标题、摘要和正文中的相对链接会按订阅源地址补全。
- 搜索基于小写化文本索引，中文、日文等无空格语言也能稳定匹配。
- 浅色 / 深色 / 跟随系统三种主题，偏好保存在本地。
- 键盘快捷键：`j/k` 切换文章，`m` 标记已读，`s` 星标，`r` 刷新，`/` 聚焦搜索，`?` 查看快捷键。
- 窄屏下自动切换为抽屉式订阅栏和全屏阅读层。

### 后台维护

- 内置调度线程，每 20 秒检查一次到期订阅。
- 抓取使用最多 3 个并发工作线程，避免占满 TOS 的轻量资源。
- 失败后按 5 / 15 / 60 / 180 / 360 分钟退避，成功后自动恢复。
- 每小时清理一次过期文章，可按保留天数和每个订阅的最大文章数配置。
- 全部数据保存在本地 SQLite 数据库，启用 WAL 和外键约束。

## 安装

安装包由仓库中的 `build.py` 生成。以 x86_64 为例：

```bash
sudo dpkg -i yuii-reader_0.1.0_x86_64.deb
```

安装脚本会：

1. 创建 `yuii-reader` 系统用户（可用时加入 `allusers` 组）。
2. 解压前端资源到 `/usr/local/yuii-reader/webui`。
3. 安装并启用 `yuiireader-system.service`，启动后等待 `/var/api/yuii-reader.sock` 出现。
4. 启动失败时把诊断信息写入 `/usr/local/yuii-reader/logs/postinst-diagnostic.txt`。

检查服务状态：

```bash
sudo systemctl status yuiireader-system.service
curl --unix-socket /var/api/yuii-reader.sock http://localhost/api/health
```

健康检查返回的 JSON 中 `"app"` 必须是 `"yuii-reader"`，只看到 HTTP 200 不足以说明服务正常。

> 卸载会删除 `/usr/local/yuii-reader`（包括数据库）。卸载前请先在设置中导出 OPML。

```bash
sudo dpkg -r yuii-reader   # 停止服务并删除应用目录
sudo dpkg -P yuii-reader   # 额外删除系统用户/组
```

## 本地开发

要求：

- Python 3.10+（CI 使用 3.12）
- Node.js 18+（仅用于前端语法检查和 `node:test`，运行时不需要 Node）

后端不使用第三方依赖。启动一个本地 TCP 实例：

```bash
YUII_READER_PORT=8787 \
YUII_READER_DATA_DIR=./data \
YUII_READER_FRONTEND_DIR=./frontend \
python backend/yuii-reader-service
```

然后访问 <http://127.0.0.1:8787/>。

### 测试

```bash
python -m compileall -q backend
python -m unittest discover -s backend/tests -v

node --check frontend/theme.js
node --check frontend/tools.js
node --check frontend/app.js
node --test tests/frontend_tools.test.js
```

测试覆盖订阅解析、编码识别、HTML 清洗、SQLite 持久化、服务层逻辑、HTTP 路由、静态资源、生命周期脚本和打包契约。网络请求在单元测试中全部被 mock，不会访问真实站点。

### 构建

```bash
python build.py --platform x86_64
python build.py --platform aarch64
```

产物写入 `dist/`：

- `yuii-reader_0.1.0_x86_64.deb` / `.sha256`
- `yuii-reader_0.1.0_aarch64.deb` / `.sha256`

构建固定文件属主、时间戳、归档条目顺序和压缩参数，同一工具链下可复现。不同 zlib 版本的压缩流可能产生不同字节，发布资产以对应 `.sha256` 为准。不要通过重命名 `.deb` 伪造架构。

## 目录结构

```text
backend/yuii_reader/     Python 服务（feeds / sanitize / store / service / server）
backend/tests/           后端单元测试与打包契约测试
frontend/                原生 HTML/CSS/JS
init.d/                  systemd 单元
DEBIAN/                  postinst / prerm / postrm 和 control
images/icons/            应用图标
docs/                    架构、目录和 TOS 7 规范说明
tests/                   前端 Node 测试
build.py                 确定性打包脚本
config.ini               TOS 应用元数据
yuii-reader.lang         14 种语言的商店文案
```

## 数据与隐私

- 数据库位置：`/usr/local/yuii-reader/data/yuii-reader.db`。
- 应用不收集遥测，不向任何统计服务上报阅读行为。
- 抓取订阅和图片时，目标站点可见的来源是本机，而不是 TOS 账号。
- 正文在服务端按白名单清洗后才下发，前端只把清洗后的 HTML 插入阅读区；标题、作者等元数据一律通过 `textContent` 渲染。
- 应用自身不做登录鉴权，访问控制由 TOS 的 iframe 代理会话负责。请勿把 socket 直接暴露到公网。

## 已知限制

- 只读取订阅源提供的正文或摘要，不做网页全文抓取。
- 不支持播客/视频 enclosure 的内嵌播放。
- 单用户本地应用，没有多账号或跨设备同步。
- 图片代理会按正文中的 http(s) 地址发起请求，本质上是一个受限的 SSRF 面；默认只应在可信内网使用。
- 本版本已完成 Windows 环境的自动化测试和打包，尚未在真实 TOS 7 设备上完成安装验收；相关清单见 `docs/TOS7_SPEC_COMPLIANCE_CN.md`。

## 反馈与维护

- 缺陷报告：使用仓库的 Bug Report 模板，附版本号、TOS 版本和复现步骤。
- 功能建议：使用 Feature Request 模板，说明使用场景而不是只描述实现方式。
- 安全问题：不要公开提 issue，见 `SECURITY.md`。
- 版本计划：见 `ROADMAP.md` 和 `CHANGELOG.md`。

## 许可证

MIT License，Copyright (c) 2026 Yuii。见 [LICENSE](LICENSE)。

## 致谢

- [Miniflux](https://github.com/miniflux/v2) 与 [FreshRSS](https://github.com/FreshRSS/FreshRSS)：产品形态和长期维护方式的参考。
- TOS 7 iframe 应用规范与社区踩坑记录：`TOS7_APP_IFRAME_DEVELOPMENT_NOTES.md`（工作区上级目录）。
