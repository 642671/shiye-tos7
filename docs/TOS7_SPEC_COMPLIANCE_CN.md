# TOS 7 规范对照与实机验收清单

本文记录拾页对 TOS 7 iframe Deb 应用规范的实现位置，以及尚未在真实设备上完成的验收项。背景根因和更多错误码见工作区的 `TOS7_APP_IFRAME_DEVELOPMENT_NOTES.md`。

> 当前状态：Windows 环境下已通过自动化测试、前端检查和双架构打包；**尚未在真实 TOS 7 设备上安装验收**。下方“实机验收”全部未打勾。

## 一、应用元数据

| 规范 | 实现 |
| --- | --- |
| 应用 ID | `config.ini` 的 `id` = `shiye` |
| 发布者 | `publisher` = `Yuii` |
| 类型 | `type` = `iframe`，`application_type` = `deb` |
| 路径 | `/shiye/` |
| 系统服务名 | `system_id` = `shiye-system` |
| 系统用户 | `user` = `shiye` |
| 最低版本 | `low_version` = `TOS7.0` |
| 图标 | `/images/icons/shiye.svg`，viewBox `0 0 64 64`，≤50 KB |
| 多语言 | `shiye.lang` 固定 14 个 section |
| 版本 | `VERSION` = `config.ini` = `DEBIAN/control` = `__version__` |

`build.py` 和 `backend/tests/test_package_contract.py` 会校验上述字段，缺一项即失败。

## 二、安装布局

| 路径 | 内容 |
| --- | --- |
| `/usr/local/shiye/bin/shiye-service` | 启动入口 |
| `/usr/local/shiye/lib/shiye/` | Python 包 |
| `/usr/local/shiye/webui/` | 解压后的前端 |
| `/usr/local/shiye/webui.bz2` | 前端压缩包 |
| `/usr/local/shiye/data/` | SQLite 数据 |
| `/usr/local/shiye/logs/` | 诊断日志 |
| `/etc/systemd/system/shiye-system.service` | systemd 单元 |
| `/var/api/shiye.sock` | Unix socket |

## 三、systemd 对照

| 规则 | 实现 |
| --- | --- |
| 不设置 `Group=<appid>` | 单元中没有 `Group=`；`postinst` 建用户时优先 `--gid allusers` |
| 不使用 `PrivateTmp=true` | 未启用，避免 `/var/api` 路径被隔离 |
| `TimeoutStopSec=10` | 已设置 |
| 响应 `SIGTERM` | `server.main` 注册 SIGTERM/SIGINT，`server.shutdown()` 后关闭服务 |
| 失败重启 | `Restart=on-failure`、`RestartSec=10` |
| 资源限制 | `MemoryMax=512M`、`CPUQuota=50%`、`LimitNOFILE=65536` |
| 沙箱 | `ProtectSystem=strict`、`ProtectHome=true`、`ReadWritePaths` 仅数据/日志/socket 目录 |

## 四、生命周期脚本

### `postinst`

- 创建 `data/`、`logs/`。
- `shiye` 用户不存在时创建；`allusers` 组存在则作为主组。
- 解压 `webui.bz2` 到 `webui/`。
- 只在 `/var/api` 不存在时创建，并保持平台默认权限（不主动改成非 `1777`）。
- 安装单元、`daemon-reload`、`enable`、`reset-failed`。
- 只删除遗留的 socket（`-S` 判断）；同路径的普通文件不会被删除。
- 启动后最多等待 25 秒，验证 `systemctl is-active --quiet` 且 socket 存在。
- 失败时写 `logs/postinst-diagnostic.txt`，包含服务状态、socket、日志和权限。

### `prerm`

- 停止服务并等待最多 12 秒。
- `disable`，仅当路径是 socket 时删除。

### `postrm`

- 删除单元和 socket，`daemon-reload`、`reset-failed`。
- `remove` / `purge` 时删除 `/usr/local/shiye`。
- `purge` 时额外删除用户和组。
- 从不删除 `/var/api` 本身。

## 五、前端对照

| 规则 | 实现 |
| --- | --- |
| iframe 标题栏占位 | `index.html` 中 56px 占位，`pointer-events: none` |
| 主体高度 | `body { padding-top: 56px }`，`.app-shell { height: calc(100vh - 56px) }` |
| 右下安全角 | 保留 20×20 区域 |
| 静态资源版本 | `app.js?v=__APP_VERSION__` 等，构建时替换 |
| 禁内联脚本 | `index.html` 无内联 `<script>` 和 `on*` 属性；CSP 为 `script-src 'self'` |
| 会话请求 | `credentials: "include"`，不手写 `Cookie` |
| GET 请求 | 不设置 `Content-Type` |
| 健康检查 | 校验 `health.app !== "shiye"`，不只看 200 |
| 代理前缀 | 并发探测 `/v2/proxy/shiye`、`/proxy/shiye`、`/shiye`、页面目录和根路径的 `/api/health` 与 `/health`，取第一个成功项 |
| 失败分类 | 区分网络异常、会话失效、代理异常、服务离线，并在失败页显示实际请求地址 |
| 图片 URL | `fixMediaUrls` 把 `api/media?` 改写为当前 API 基地址 |

## 六、打包

- `python build.py --platform x86_64` → `dist/shiye_0.2.1_x86_64.deb`
- `python build.py --platform aarch64` → `dist/shiye_0.2.1_aarch64.deb`
- 每个包附带 `.sha256`。
- 固定 uid/gid 0、mtime `1704067200`、gzip mtime 0，构建可复现。
- `verify_package` 会重新读取 ar 成员，检查 control 版本/架构、脚本可执行位、webui 必需文件、`__APP_VERSION__` 替换和 `?v=` 参数。
- 不允许通过重命名文件伪造架构。

## 七、实机验收（未完成）

在真实 TOS 7 设备上按顺序执行，并记录设备型号、TOS 版本、架构和结果。

### 安装

- [ ] `sudo dpkg -i shiye_0.2.1_<arch>.deb` 返回 0。
- [ ] `systemctl status shiye-system.service` 为 `active (running)`。
- [ ] `ls -l /var/api/shiye.sock` 存在，所有者为 `shiye`。
- [ ] `/var/api` 权限仍为平台默认（通常 `1777`），未被安装脚本修改。
- [ ] `curl --unix-socket /var/api/shiye.sock http://localhost/api/health` 返回 `"app":"shiye"`。
- [ ] `logs/postinst-diagnostic.txt` 不存在（存在则说明启动曾失败）。

### iframe

- [ ] 从 TOS 应用中心能看到图标、名称、发布者 `Yuii` 和 14 种语言文案。
- [ ] 打开后顶部 56px 不被应用内容遮挡，底部 20×20 安全角无按钮。
- [ ] 浏览器控制台无 CSP 报错、无 404、无混合内容警告。
- [ ] 窗口缩放和最大化后布局正常。
- [ ] 移动端（若允许打开）抽屉和阅读层可正常返回。

### 功能

- [ ] 添加一个 HTTPS RSS 源成功，标题、图标、文章出现。
- [ ] 添加一个 Atom 源成功。
- [ ] 添加一个 GBK 编码源，中文不乱码。
- [ ] 输入站点首页时能自动发现 feed。
- [ ] 文章正文无脚本、无跟踪像素，图片通过 `api/media` 加载。
- [ ] 已读、未读、星标、全部已读、搜索、分页正常。
- [ ] 新建/重命名/删除分类正常。
- [ ] OPML 导出后再导入，订阅和分类正确。
- [ ] 手动刷新和后台定时刷新都能更新文章。
- [ ] 弱网/断网时 UI 显示错误并可重试，不白屏。

### 生命周期

- [ ] 升级安装（同版本重装或更高版本）后数据保留。
- [ ] `systemctl stop` 在 10 秒内完成，socket 被清理。
- [ ] `systemctl start` 后 socket 重新出现，健康检查恢复。
- [ ] 设备重启后服务自动启动。
- [ ] `dpkg -r` 后服务、单元和 socket 均移除。
- [ ] `dpkg -P` 后用户/组被删除，`/var/api` 仍存在且权限未变。

### 诊断

- [ ] 人为制造启动失败（例如临时改坏 `ExecStart`），确认 `postinst-diagnostic.txt` 有可读信息。
- [ ] `journalctl -u shiye-system -n 100` 中无反复崩溃循环。

## 八、已知未覆盖项

- TOS 7.0 与 7.1 内核差异未逐一实机验证。
- 休眠/唤醒后的调度器行为未验证。
- 大订阅量（>100 个 feed）下的内存和刷新耗时未实测。
- 图片代理在 TOS 的 HTTPS 代理环境下未实机验证。
- 应用中心上架流程和审核要求未走通。
