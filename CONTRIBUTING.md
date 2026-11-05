# 贡献指南

感谢你帮助改进拾页。这个项目面向 TOS 7 的长期维护，稳定性和可预测性比功能数量更重要。

## 开始之前

1. 先搜索已有 issue，避免重复。
2. 缺陷请附：应用版本、TOS 版本、设备架构、复现步骤、预期结果和实际结果。
3. 新功能请先说明使用场景和当前的不便，不要只给实现方案。
4. 安全问题不要公开提 issue，按 `SECURITY.md` 私下报告。

## 开发环境

- Python 3.10+（CI 使用 3.12）。
- Node.js 18+，仅用于前端检查和测试。
- Windows 下可用 PowerShell；生命周期脚本和构建逻辑必须保持与 Linux 兼容。

后端只允许 Python 标准库。新增第三方依赖需要先讨论，并验证 TOS 内置 Python 环境可用。前端不引入打包链、框架或运行时依赖。

## 修改流程

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

提交前必须全部通过。只改与任务直接相关的文件，不要顺便重构或升级依赖。

### 前端约定

- 页面由 `frontend/index.html` 提供固定骨架，动态内容用 `createElement` / `textContent` 构建。
- 用户可见的元数据不得通过 `innerHTML` 插入；只有服务端已清洗的正文可以写入阅读区。
- 静态资源引用必须带 `?v=__APP_VERSION__`，避免 TOS 浏览器缓存旧文件。
- API 请求使用 `credentials: "include"`，GET 不设置 `Content-Type`，不要手写 `Cookie`。
- 健康检查必须校验响应中的 `app` 字段，不能只看状态码。
- 新增 DOM id 时同步更新 `backend/tests/test_package_contract.py` 中的契约列表。

### 后端约定

- 网络抓取和解析集中在 `feeds.py`，清洗集中在 `sanitize.py`，不要在 `service.py` 里重复实现。
- 所有 SQL 通过 `store.py`，不要在路由层直接访问数据库。
- 用户输入和订阅源内容都视为不可信：限制长度、校验类型、捕获异常并转换为 `ServiceError`。
- 保持 `AppService` 的线程安全边界；调度器和 HTTP 线程共享同一个 `Store`。
- 新增 API 时同时补 `backend/tests/test_server_helpers.py` 和 `test_service.py`。

### TOS 7 约束

修改打包或服务行为前，先阅读工作区的 `TOS7_APP_IFRAME_DEVELOPMENT_NOTES.md`。以下规则不可违反：

- systemd 单元不设置 `Group=<appid>`。
- 不启用 `PrivateTmp=true`。
- 不修改 `/var/api` 的所有者或权限（通常为 `1777` sticky）。
- `postinst` 必须 `reset-failed`，启动后验证服务 active 且 socket 存在。
- `TimeoutStopSec=10`，服务必须响应 `SIGTERM` 并快速退出。
- 生命周期脚本使用 POSIX `sh`，不使用 Bash 专属语法。

## 版本发布

发布前按顺序检查：

1. 更新 `VERSION`。
2. 同步 `config.ini` 的 `version`。
3. 同步 `DEBIAN/control` 的 `Version`。
4. 同步 `backend/shiye/__init__.py` 的 `__version__`。
5. 更新 `CHANGELOG.md`。
6. 更新 `shiye.lang` 中 14 个语言的 `release_note`，使其以新版本号开头。
7. 运行完整测试和两个架构的构建。
8. 打 tag `vX.Y.Z` 并推送，CI 会生成构建产物。

`backend/tests/test_package_contract.py` 会校验上述版本一致性，缺一处就会失败。

## 提交与 Pull Request

- 提交信息使用简短的祈使句，例如 `Fix feed icon fallback`。
- 一个 PR 只做一件事；避免混合格式化、重构和功能改动。
- PR 描述写清楚：改了什么、为什么、如何验证、有哪些未验证项。
- 不要提交 `dist/`、`data/`、`logs/`、缓存或生成的 `.deb`。
