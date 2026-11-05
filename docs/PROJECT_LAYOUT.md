# 项目目录

```text
shiye-tos7/
├─ VERSION                          当前版本号（0.1.0）
├─ config.ini                       TOS 应用元数据（JSON）
├─ shiye.lang                       14 种语言的商店文案
├─ shiye.env                        systemd 环境变量
├─ LICENSE                          MIT
├─ README.md                        安装、开发、限制
├─ CHANGELOG.md                     版本记录
├─ CONTRIBUTING.md                  贡献流程
├─ SECURITY.md                      安全模型与漏洞报告
├─ CLAUDE.md                        自动化助手工作约定
├─ ROADMAP.md                       版本方向
│
├─ backend/
│  ├─ shiye-service                  启动入口（设置 sys.path 后调用 main）
│  ├─ shiye/
│  │  ├─ __init__.py                APP_ID / APP_NAME / __version__
│  │  ├─ feeds.py                   HTTP 抓取、编码识别、RSS/RDF/Atom、OPML
│  │  ├─ sanitize.py                HTML 白名单、跟踪参数、图片代理、文本提取
│  │  ├─ store.py                   SQLite schema 与数据访问
│  │  ├─ service.py                 业务编排、调度器、线程池
│  │  ├─ server.py                  HTTP 路由、静态资源、Unix/TCP 监听
│  │  └─ util.py                    时间、整数、文本和 LIKE 工具
│  └─ tests/
│     ├─ test_feeds.py              抓取、解码、解析、发现、OPML
│     ├─ test_sanitize.py           清洗规则和文本提取
│     ├─ test_store.py              数据模型、筛选、搜索、清理、设置
│     ├─ test_service.py            业务逻辑（网络 mock）
│     ├─ test_server_helpers.py     HTTP 路由和静态资源（TCP 端口）
│     └─ test_package_contract.py   版本、语言、systemd、生命周期、前端契约
│
├─ frontend/
│  ├─ index.html                    页面骨架、对话框、ID 契约
│  ├─ styles.css                    设计系统、响应式、主题
│  ├─ app.js                        状态、API 客户端、渲染、快捷键
│  ├─ tools.js                      纯函数（UMD，可被 Node 测试）
│  ├─ theme.js                      主题应用（最先执行）
│  └─ favicon.svg                   浏览器图标
│
├─ tests/
│  └─ frontend_tools.test.js        node:test 前端纯函数测试
│
├─ init.d/
│  └─ shiye-system.service           systemd 单元
│
├─ DEBIAN/
│  ├─ control                       包元数据
│  ├─ postinst                      建用户、解压、装服务、启动校验
│  ├─ prerm                         停止、禁用、清理 socket
│  └─ postrm                        卸载清理（purge 删除用户/组）
│
├─ images/icons/
│  └─ shiye.svg                     应用图标（64×64 viewBox）
│
├─ docs/
│  ├─ ARCHITECTURE.md               运行时、数据模型、API、打包
│  ├─ PROJECT_LAYOUT.md             本文件
│  └─ TOS7_SPEC_COMPLIANCE_CN.md    TOS 7 规范对照与实机清单
│
├─ .github/
│  ├─ workflows/build.yml           CI：测试 + 双架构构建 + tag 发布
│  └─ ISSUE_TEMPLATE/
│     ├─ bug_report.yml
│     ├─ feature_request.yml
│     └─ config.yml
│
├─ build.py                         确定性打包与产物校验
├─ .gitignore
└─ .gitattributes
```

## 运行时与源码的对应关系

| 安装后路径 | 源码 |
| --- | --- |
| `/usr/local/shiye/webui/` | `frontend/` 打包为 `webui.bz2` 后解压 |
| `/usr/local/shiye/lib/shiye/` | `backend/shiye/` |
| `/usr/local/shiye/bin/shiye-service` | `backend/shiye-service` |
| `/etc/systemd/system/shiye-system.service` | `init.d/shiye-system.service` |
| `/usr/local/shiye/data/shiye.db` | 运行时生成 |
| `/var/api/shiye.sock` | 运行时生成 |

## 哪些文件不要手改

- `dist/`：由 `build.py` 生成。
- `__pycache__/`、`data/`、`logs/`：运行时产物，已在 `.gitignore` 中。
- `.deb`、`.sha256`：发布资产，只能由构建生成。
- `shiye.lang` 的 section 数量和字段名：打包和契约测试会校验。

## 新增文件时的检查清单

1. 后端模块是否需要加入 `build.py` 的 `REQUIRED_BACKEND` 和 `test_package_contract.py`。
2. 前端文件是否需要加入 `REQUIRED_FRONTEND`、`server.py` 的 `STATIC_FILES` 和 `index.html` 的版本化引用。
3. 是否需要在 `README.md`、`docs/` 或 `CHANGELOG.md` 中说明。
4. 是否引入第三方依赖（默认不允许）。
