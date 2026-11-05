# Changelog

本项目的版本记录遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 和 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### Planned

- 实机 TOS 7 安装验收记录与截图。
- 正文阅读进度和字体大小设置。
- 订阅源健康状态面板。

## [0.1.0] - 2026-11-05

首个公开版本。

### Added

- RSS 2.0、RSS 1.0（RDF）、Atom 1.0 解析，支持站点自动发现和 GBK/Big5 等编码。
- 条件请求（ETag / Last-Modified）、gzip/deflate、重定向与体积限制。
- 订阅管理、分类管理、启用/停用、手动刷新。
- 三栏阅读界面，未读/全部/星标视图，按订阅或分类筛选。
- 已读、未读、星标、全部标记已读。
- 服务端 HTML 白名单清洗，移除脚本、事件属性、iframe、跟踪参数和跟踪像素。
- 图片本地代理 `api/media`，带体积和类型限制。
- 搜索（小写化索引，兼容 CJK）、分页加载。
- OPML 2.0 导入导出。
- 浅色/深色/跟随系统主题，键盘快捷键，移动端抽屉布局。
- 后台调度：20 秒轮询、3 线程并发、5/15/60/180/360 分钟退避、每小时清理。
- SQLite 持久化（WAL、外键、`PRAGMA user_version` 迁移位）。
- TOS 7 systemd 单元、`postinst` 启动校验和失败诊断、POSIX 生命周期脚本。
- 确定性 `.deb` 打包（固定属主、时间戳、条目顺序），x86_64 / aarch64、SHA256 校验和、14 语言商店文案。
- 后端单元测试、前端 Node 测试和打包契约测试。

### Changed

- 发布前将应用名调整为「拾页」，应用 ID、仓库名、安装路径、服务名和包名统一使用 `shiye`；发布者仍为 Yuii。

### Security

- 前端 CSP 禁止内联脚本和外部脚本源。
- 请求使用 `credentials: include`，由 TOS 代理维护会话，不手写 Cookie。
- 服务默认只监听 Unix socket `/var/api/shiye.sock`。

### Known limitations

- 尚未在真实 TOS 7 设备完成安装验收。
- 不支持全文抓取、播客播放、多用户或跨设备同步。
