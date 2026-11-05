# 架构说明

拾页是一个单进程、单用户的本地阅读收件箱。它刻意保持小：一个 Python 服务、一个 SQLite 文件、一组静态前端资源，没有消息队列、缓存中间件或前端构建链。订阅文章和手动保存的网页链接共享同一套文章模型。

## 运行时拓扑

```text
TOS 7 浏览器 iframe
        │  HTTPS + TOS 会话
        ▼
TOS iframe 代理 (/v2/proxy/shiye/...)
        │  HTTP over Unix socket
        ▼
/var/api/shiye.sock
        │
        ▼
systemd: shiye-system.service
  └─ python3 backend/shiye-service
       ├─ AppService（业务编排、调度器、线程池）
       ├─ Store（SQLite + WAL）
       ├─ feeds.py（HTTP 抓取 + 解析）
       ├─ readable.py（保存链接的正文提取）
       └─ sanitize.py（HTML 清洗）
        │
        ▼
/usr/local/shiye/data/shiye.db
```

服务默认只监听 Unix socket。设置 `SHIYE_PORT` 时改为监听本机 TCP，仅用于开发调试。

## 请求处理

1. `server.py` 接收请求，`normalize_path` 去掉 `/v2/proxy/shiye`、`/proxy/shiye`、`/shiye` 前缀。
2. `/api/*` 进入 `_route_api`，按方法分派到 `AppService`。
3. 非 API 路径作为静态文件，从 `frontend/` 读取；`index.html` 响应 CSP，其他资源按扩展名设置类型和缓存策略。
4. 所有业务错误转换为 `ServiceError`，统一输出 `{"error", "code"}` 和对应状态码。
5. 未捕获异常记录日志并返回 500，不泄漏堆栈。

### HTTP API

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| GET / HEAD | `/api/health`、`/health` | 健康检查，含应用标识、版本、订阅/文章计数；带 TOS 前缀的路径同样可用 |
| GET | `/api/feeds` | 列出订阅 |
| POST | `/api/feeds` | 添加订阅（支持 URL 自动发现和分类） |
| GET / PATCH / DELETE | `/api/feeds/{id}` | 查看、修改、删除订阅 |
| POST | `/api/feeds/{id}/refresh` | 立即刷新单个订阅 |
| GET | `/api/articles` | 文章列表，支持 `feed_id`、`category_id`、`unread`、`starred`、`saved`、`q`、`limit`、`offset` |
| GET / DELETE | `/api/articles/{id}` | 文章正文 / 移出稍后读（仅保存的文章） |
| POST | `/api/articles/{id}/read` | 设置已读状态 |
| POST | `/api/articles/{id}/star` | 切换或设置星标 |
| POST | `/api/articles/{id}/extract` | 重新抓取并提取已保存文章的正文 |
| POST | `/api/saved` | 保存任意网页链接；`fallback=true` 时只保存链接 |
| POST | `/api/articles/mark-read` | 批量设置已读 |
| POST | `/api/articles/mark-all-read` | 按订阅/分类全部已读 |
| GET / POST | `/api/categories` | 分类列表 / 创建 |
| PATCH / DELETE | `/api/categories/{id}` | 重命名 / 删除 |
| GET / PUT | `/api/settings` | 读取 / 更新设置（服务端钳制范围） |
| GET | `/api/stats` | 统计信息 |
| POST | `/api/refresh` | 触发全部刷新（异步） |
| GET / POST | `/api/opml` | 导出 / 导入 OPML |
| GET | `/api/media?url=...` | 图片代理 |

`/api/health` 和 `/health` 的响应正文包含 `"app": "shiye"`。前端和运维脚本都必须校验这个字段。前端会并发探测平台可能使用的多种代理前缀，以第一个返回正确应用标识的地址作为 API 基地址；连接失败时按网络、会话、代理、服务四层分类并显示失败地址。

## 数据模型

SQLite 使用 WAL、`synchronous=NORMAL`、外键约束和 10 秒 busy timeout。`PRAGMA user_version` 记录 schema 版本，当前为 2。旧版数据库启动时自动补充 `feeds.kind`、`articles.site_name`、`articles.word_count` 和 `articles.reading_minutes`，无需重建。

### `categories`

`id`、大小写不敏感的 `name`（唯一）、`position`、`created_at`。

### `feeds`

订阅源元数据和抓取状态：`url`（唯一）、`title`、`site_url`、`description`、`icon_url`、`kind`（`rss` / `saved`）、条件请求字段 `etag` / `last_modified`、`last_fetch_at`、`last_success_at`、`next_fetch_at`、`error_count`、`last_error`、`disabled`。分类删除时 `category_id` 置空。

`kind='saved'` 是一个隐藏的内置来源（`shiye:saved`，标题「稍后读」），不对用户展示，也不参与定时抓取。把保存链接放进同一条 `articles` 路径，避免 `feed_id` 可空带来的筛选、搜索和清理分支；删除稍后读文章时只删除该文章本身。

### `articles`

`UNIQUE(feed_id, guid)` 保证重复抓取幂等。除正文外保存 `summary`（纯文本摘要）、`site_name`、`word_count`、`reading_minutes`、`published_at`、`fetched_at`、`is_read`、`is_starred`，以及用于搜索的 `search_text`（标题、作者、摘要、正文转小写后的拼接）。删除订阅级联删除文章。

### `settings`

键值表。默认值：`refresh_interval=30`（5–1440 分钟）、`retention_days=90`（0–3650，0 表示不按时间清理）、`max_per_feed=500`（50–5000）、`auto_refresh=true`。写入时统一钳制，读取时补默认值。

## 抓取与解析

`feeds.py` 的职责边界：

1. `normalize_feed_url`：只接受 http/https，补全 scheme，拒绝空主机。
2. `fetch`：带 `If-None-Match` / `If-Modified-Since`，最多 5 次重定向，20 秒超时，5 MiB 上限，支持 gzip/deflate。
3. `decode_body`：BOM → XML 声明 → UTF-8 → GB18030/Big5 回退。
4. `parse_feed`：按根元素分派 RSS / RDF / Atom；统一输出 `ParsedFeed` 和 `ParsedEntry`。
5. `discover_feed_links`：添加订阅时若内容不是 feed，则解析 HTML 中的 `<link rel="alternate">` 再试一次。
6. `fetch_image`：类型白名单 + 8 MiB 上限，供图片代理使用。
7. `parse_opml` / `build_opml`：OPML 2.0 双向转换。

日期解析同时支持 RFC 822 和 ISO 8601；未来时间会被钳制到当前时间，避免订阅源错误日期把文章顶到列表顶部。

## 保存链接与正文提取

`POST /api/saved` 是保存链接的唯一入口，流程如下：

1. `normalize_page_url` 只接受 http/https；已有同 URL 且带正文的稍后读文章直接返回，不重复抓取。
2. 普通模式调用 `fetch_page` 抓取原页面，`readable.extract_article` 在标准库 DOM 树上按容器文本量、链接密度、段落比例等打分，选出正文容器。
3. 提取标题、作者、站点名、摘要和正文；支持 JSON-LD `Article` 元数据、`og:` / `name="author"` 回退、懒加载图片属性提升，并去掉与标题重复的首个标题。
4. 正文交给 `sanitize_fragment` 清洗，计算字数与预计阅读时间，写入隐藏的稍后读来源。
5. `fallback=true` 或提取失败时，保存标题、URL 和摘要的“仅链接”记录；之后可在阅读区调用 `POST /api/articles/{id}/extract` 重新提取。

提取器不执行 JavaScript，也不绕过登录或付费墙。页面依赖前端渲染时，通常只能得到标题、摘要或仅链接记录。抓取沿用 `fetch_page` 的超时、体积、重定向和内容类型限制。

## 内容清洗

`sanitize.py` 基于 `html.parser.HTMLParser` 实现流式白名单：

- 允许常见排版标签（段落、标题、列表、表格、代码、图片、链接等），未知标签丢弃但保留文本。
- 禁止标签（`script`、`style`、`iframe`、`object`、`form`、`svg` 等）连同内容一起跳过。
- 属性按标签白名单过滤；`href` 只允许 http/https/mailto，`src` 只允许 http/https 或受限 data 图片。
- 相对链接按订阅源地址补全，并移除 `utm_*`、`fbclid` 等跟踪参数。
- 图片改写为 `api/media?url=...`，加 `loading="lazy"` 和 `referrerpolicy="no-referrer"`。
- 1×1 图片和常见跟踪像素直接丢弃。
- 输出超过上限时截断并追加省略号。

`AppService` 在入库前完成清洗，数据库里保存的是已清洗 HTML，因此前端渲染路径不再二次解释原始内容。

## 调度与并发

- `AppService.start_background()` 启动 `shiye-scheduler` 线程，每 20 秒查询一次 `next_fetch_at <= now` 的订阅。
- 抓取任务提交到 `ThreadPoolExecutor(max_workers=3)`，避免慢站点阻塞调度循环。
- 成功后清空 `error_count`，按全局 `refresh_interval` 计算下次抓取时间。
- 失败按 5 / 15 / 60 / 180 / 360 分钟退避，达到上限后保持 360 分钟。
- 每小时执行一次 `prune`：按保留天数删除旧文章，并确保每个订阅不超过 `max_per_feed`。
- `close()` 先停调度线程，再关线程池和数据库；SIGTERM 触发 `server.shutdown()`，不无限阻塞。

## 前端

`app.js` 是唯一的状态所有者：

- `Api` 客户端自动探测代理前缀，统一处理 JSON、错误和网络失败。
- `state` 保存当前视图、文章列表、选中项、订阅和分类；`saved` 是独立的筛选类型。
- 侧栏、列表、阅读区分别由 `renderSidebar` / `renderArticleList` / `renderReader` 重建。
- 所有用户可见文本通过 `textContent` 写入；只有已清洗正文进入阅读区。
- 保存链接对话框有三种结果：提取并保存、仅保存链接、已存在；阅读区对“仅链接”和“只有摘要”分别给出上下文操作。
- 主题在 `theme.js` 中最早执行，避免首屏闪烁；主题按钮在浅色/深色间直接切换，设置页仍可选“跟随系统”。
- 阅读进度按 `scrollTop / (scrollHeight - clientHeight)` 计算，正文字号写入根元素 CSS 变量，两者都保存在本地。
- `tools.js` 以 UMD 形式暴露纯函数，便于 Node 直接测试。
- 窄屏（≤980px）切换抽屉侧栏，阅读时使用全屏 overlay。

## 打包

`build.py` 负责：

1. 校验版本一致性、14 语言文件、图标、服务契约和前后端文件完整性。
2. 在临时目录组装 `usr/local/shiye/`，把前端打成 `webui.bz2`。
3. 用固定 mtime、uid/gid 0、GNU tar 生成 `control.tar.gz` 和 `data.tar.gz`。
4. 组装 ar 归档，写入 md5sums 和 SHA256。
5. 重新读取 `.deb` 验证成员、版本、架构、脚本权限和 webui 内容。

构建固定属主、时间戳和归档条目顺序，消除文件系统枚举顺序带来的差异。压缩流由 zlib 生成，不同 zlib 版本的字节可能不同，因此跨环境复现以归档内容一致为准，发布资产以对应 `.sha256` 为准。

## 失败处理

- `postinst` 启动失败时写 `logs/postinst-diagnostic.txt`，包含服务状态、socket、最近日志和目录权限。
- 服务启动时若 socket 路径已存在且不是 socket，会拒绝删除并报错，避免误删普通文件。
- 抓取失败只影响单个订阅，不中断调度循环；错误写入 `feeds.last_error` 供 UI 展示。
- 数据库打开失败时服务直接退出，由 systemd 记录日志并触发 `Restart=on-failure`。

## 测试策略

- 后端单元测试用 mock 替换网络，覆盖解析、正文提取、清洗、存储、服务、路由和契约。
- 前端纯函数用 `node:test` 覆盖。
- 打包契约测试同时检查静态文件、语言文件、生命周期脚本和 systemd 单元。
- 发布前用本地 TCP 实例和 Playwright 做桌面/移动端冒烟检查，覆盖保存链接、仅链接提示、主题切换和空状态；截图不写入数据库或真实站点。
- 不依赖真实 TOS 设备的测试必须在 Windows/Linux 上都能跑；Unix socket 相关代码做了平台保护。
