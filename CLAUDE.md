# CLAUDE.md

本文件面向在此仓库中工作的自动化助手。仓库根目录是 `shiye-tos7/`，它通常位于多项目工作区 `dev-test/` 之下；如果存在更上层的 `AGENTS.md`，同时遵守。

## 项目定位

拾页是 TOS 7 的 iframe Deb 应用：

- 后端：Python 3.10+ 标准库，运行在 TOS 内置 Python 上。
- 前端：原生 HTML/CSS/JavaScript，无构建链、无框架、无 CDN。
- 通信：systemd 服务监听 `/var/api/shiye.sock`，由 TOS 代理转发。
- 数据：`/usr/local/shiye/data/shiye.db`（SQLite）。
- 发布：`build.py` 生成确定性 `.deb`，支持 `x86_64` / `aarch64`。

## 常用命令

```bash
python -m compileall -q backend
python -m unittest discover -s backend/tests -v

node --check frontend/theme.js
node --check frontend/tools.js
node --check frontend/app.js
node --test tests/frontend_tools.test.js

python build.py --platform x86_64
python build.py --platform aarch64
```

不要用 `-SkipTests` 或跳过测试来“完成”构建。构建失败时修代码，不要改 `dist/` 里的产物。

## 代码地图

| 路径 | 职责 |
| --- | --- |
| `backend/shiye/feeds.py` | URL 规范化、抓取、编码识别、RSS/RDF/Atom 解析、OPML |
| `backend/shiye/sanitize.py` | HTML 白名单清洗、跟踪参数、图片代理、纯文本提取 |
| `backend/shiye/store.py` | SQLite schema、CRUD、筛选、搜索、清理、设置钳制 |
| `backend/shiye/service.py` | 业务编排、调度器、线程池、错误映射 |
| `backend/shiye/server.py` | HTTP 路由、静态资源、CSP、Unix/TCP 监听、SIGTERM |
| `frontend/app.js` | UI 状态、API 客户端、三栏渲染、对话框、快捷键 |
| `frontend/tools.js` | 可被 Node 测试的纯函数（UMD） |
| `build.py` | 版本/语言/服务契约校验、tar/ar 打包、产物校验 |

## 不可破坏的契约

1. **版本四处同步**：`VERSION`、`config.ini`、`DEBIAN/control`、`backend/shiye/__init__.py`。`test_package_contract.py` 会校验。
2. **语言文件**：`shiye.lang` 必须是 14 个固定 section，UTF-8 无 BOM、LF 换行，`auth = "Yuii"`，`release_note` 以版本号开头。
3. **systemd**：不写 `Group=`，不启用 `PrivateTmp=true`，`TimeoutStopSec=10`，`ExecStopPost` 清理 socket。
4. **postinst**：`reset-failed`、启动后等待 active 和 socket、失败写 `logs/postinst-diagnostic.txt`。
5. **前端**：静态资源带 `?v=__APP_VERSION__`；`index.html` 不写内联脚本和 `on*` 属性；API 请求 `credentials: "include"`，GET 不带 `Content-Type`。
6. **健康检查**：响应正文必须含 `"app": APP_ID`；前端检测 `health.app !== APP_ID`。
7. **清洗**：任何进入阅读区的 HTML 必须先经 `sanitize_fragment`；元数据用 `textContent`。
8. **无第三方 Python 依赖**：确需新增时先获得用户确认。

## 修改习惯

- 先读相关模块和测试，再改代码；不要凭接口名猜行为。
- 修 bug 时补一个能复现的测试。
- 新增 API 时同步更新 `test_server_helpers.py` 和 `test_service.py`。
- 新增 DOM id 时同步更新 `test_package_contract.py` 的契约列表。
- 只改任务相关文件，不做顺手重构。
- 不创建提交、不推送、不打 tag，除非用户明确要求。
- 不运行 `git clean`、`git reset --hard` 或强制删除未提交改动。

## 平台注意

- 开发机可能是 Windows：Unix socket 不可用，测试通过 TCP 端口的 `make_server(host=..., port=0)` 完成；`socket.AF_UNIX` 做了条件保护。
- 临时目录清理在 Windows 上容易遇到 `WinError 32`，测试的 cleanup 顺序必须是先注册临时目录、后注册 `service.close`（LIFO 保证先关数据库）。
- 构建脚本需要 GNU tar；Windows 上优先在 Git Bash 或 CI 的 Linux 环境执行完整构建。
- 真实 TOS 7 验收清单见 `docs/TOS7_SPEC_COMPLIANCE_CN.md`；没有实机验证时，在交付说明中明确写出未验证项。
