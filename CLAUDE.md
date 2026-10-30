# Moyuren API

摸鱼日历图片生成服务 | FastAPI + Playwright | Python 3.12

## 架构

外部数据源（新闻/节假日/趣味内容/KFC/金价/股票指数/每日英语）→ 新闻/日级缓存服务与股票 TTL 缓存 → DataComputer（CalendarService 提供农历/节气/时区）→ ImageRenderer（Playwright）→ JPEG/WebP 变体 → CacheDir → API/RSS/Webhook

## 核心模块

| 模块 | 文件 | 职责 |
|------|------|------|
| 入口 | `app/main.py` | FastAPI 生命周期、健康/就绪检查、路由与静态目录注册 |
| 指标 | `app/core/metrics.py` | 专用 Prometheus 注册表、生成/渲染耗时、获取结果、通知结果与实例健康指标 |
| 指标 API | `app/api/metrics.py` | 可选根路径 `/metrics` 文本导出 |
| 启动 | `app/core/bootstrap.py` | 服务构建、定时任务注册、启动缓存验证 |
| 图片 API | `app/api/v1/moyuren.py` | 多编码响应、图片变体选择、条件请求与缺失缓存自动恢复 |
| Ops API | `app/api/v1/ops.py` | 仅 POST，Bearer 鉴权的生成、缓存清理与无副作用模板预览 |
| 模板 API | `app/api/v1/templates.py` | 模板列表查询 |
| RSS API | `app/api/v1/feed.py` | 最近每日图片 RSS 2.0 订阅源、条件请求与缓存 |
| API 公共工具 | `app/api/v1/common.py` | 图片 URL、ETag 与条件请求校验 |
| 服务容器 | `app/core/services.py` | AppServices 服务容器 |
| 文件写入 | `app/core/fileutil.py` | 按启动时 umask 设置原子输出权限，支持文本/字节与可选 fsync |
| 日级缓存 | `app/services/daily_cache.py` | 日级缓存抽象基类（自动过期、降级） |
| 缓存清理 | `app/services/cache.py` | 缓存清理服务 |
| 获取 | `app/services/fetcher.py` | 异步并行 HTTP 请求（主源 + 公共实例智能切换 + 静态镜像兜底） |
| 实例路由 | `app/services/instances.py` | 解析 60s 公共实例列表文档（带 TTL 缓存）并在主源/实例间智能切换 |
| 实例配置 | `config.yaml` `instances.*` | 全局 60s 公共实例清单（news/fun_content/crazy_thursday/gold_price 共享） |
| 日历 | `app/services/calendar.py` | 农历、节气、时区管理与法定休假/补班判断（CalendarService） |
| 节假日 | `app/services/holiday.py` | 中国法定节假日数据获取与处理 |
| 趣味内容 | `app/services/fun_content.py` | 随机获取冷笑话/一言/段子（主源不可用时切换公共实例） |
| KFC | `app/services/kfc.py` | 疯狂星期四文案（周四刷新，主源不可用时切换公共实例） |
| 金价 | `app/services/gold_price.py` | 每日金价获取与缓存（主源不可用时切换公共实例） |
| 股票指数 | `app/services/stock_index.py` | 大盘指数行情与 TTL 内存缓存（StockIndexService） |
| 每日英语 | `app/services/daily_english.py` | 每日英语单词服务（ECDICT + 随机 API） |
| 计算 | `app/services/compute.py` | 原始数据 → 模板上下文，合并假期内节日，按业务快照计算休息日/下班倒计时与数据新鲜度 |
| 渲染 | `app/services/renderer.py` | Jinja2 + Playwright 截图与无文件写入的 JPEG bytes 预览；通过虚拟域名提供本地 WOFF2 字体，Google Fonts 仅作缺字回退 |
| 浏览器 | `app/services/browser.py` | Playwright 浏览器生命周期、断连后下次请求重启与新建页面遇到关闭错误时重试一次 |
| 预览样例 | `app/services/preview_sample.py` | 确定性离线样例与只读日级磁盘缓存组装（逐来源回退，不联网） |
| 模板发现 | `app/services/template_discovery.py` | 按文件名/mtime/大小指纹热加载模板，解析渲染与描述 meta 标签 |
| 图片生成 | `app/services/generator.py` | 图片生成流水线（文件锁防并发、缓存/变体写入与通知触发） |
| Webhook 通知 | `app/services/notifier.py` | 生成成功后异步推送企业微信/飞书/钉钉/Telegram/通用 JSON，签名、重试与每日去重 |
| 图片变体 | `app/services/variants.py` | Pillow 在线程中从 JPEG 原子生成 WebP/缩小图片，失败隔离；API 可按需补建 |
| 调度 | `app/core/scheduler.py` | APScheduler 定时任务 |
| 配置 | `app/core/config.py` | YAML + 环境变量 |

## 快速启动

本地：`pip install -r requirements.txt && playwright install chromium && uvicorn app.main:app --reload`
Docker：`docker compose up -d`，配置覆盖统一使用 `MOYUREN_` 前缀
优先使用：`~/miniconda3/bin/python`

## API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET/HEAD | `/healthz` | 存活检查，返回 `{"status":"ok"}` |
| GET/HEAD | `/readyz` | 就绪检查；配置/缓存可用且启动任务结束时 200，否则 503，浏览器或当日数据未就绪显示 `degraded` |
| GET | `/metrics` | Prometheus 文本指标；`metrics.enabled` 默认 false，未启用时返回 404 |
| GET | `/api/v1/moyuren` | `date`、`template`、`detail=false`、`encode=json\|text\|markdown\|image\|url`；图片/URL 可传 `format=jpeg\|webp`、正整数 `width`，当日缓存或原图缺失时自动生成 |
| GET/HEAD | `/api/v1/feed.xml` | RSS 2.0；`template` 默认配置模板，`limit` 为 1..`feed.max_items`，支持 ETag/Last-Modified，关闭 feed 时 404 |
| GET | `/api/v1/templates` | 返回 `{data:[{name,description,image,show_daily_english}]}`，公开缓存 5 分钟 |
| POST | `/api/v1/ops/generate` | Bearer 鉴权同步生成，`no-store`；并发生成返回 503 + `Retry-After: 10` |
| POST | `/api/v1/ops/cache/clean` | Bearer 鉴权；可选正整数 `keep_days`，`no-store` |
| POST | `/api/v1/ops/templates/preview` | Bearer 鉴权；JSON `template`、可选 `date`、`data=cached\|sample`（默认 cached），返回 JPEG/no-store，锁超时返回 503 |
| GET/HEAD | `/static/{filename}` | 静态图片 |

## 配置

主配置：`config.yaml`。顶层对象的直接字段可用 `MOYUREN_<SECTION>_<FIELD>` 覆盖（如 `MOYUREN_METRICS_ENABLED`）；整个顶层段可传 JSON；例如深层渲染字段用 `MOYUREN_TEMPLATES_CONFIG` JSON，对象合并、列表整体替换。更深层字段和 `data_sources` 列表没有展开式环境变量。优先级为进程环境 → `.env` → YAML → 默认值；无前缀的 `<SECTION>_<FIELD>` 不参与配置覆盖。

股票指数只接受显式 `secid/name/market` 对象；节假日代理统一使用 `network.ghproxy_urls`，不接受 `mirror_urls`。词典 `db_path` 相对路径完整拼接到缓存根目录，不剥离 `cache/` 前缀。新闻使用 `DataFetcher.fetch()`；日级新闻缓存保留 `{"news": ...}` 聚合结构。节假日日缓存只接受含 `holidays` 的对象，旧列表触发重建；历史图片与每日 JSON 保留读取支持。

关键配置项：

- `server.*` / `timezone.*` / `paths.*`：监听 `host`/`port`、公开 `base_domain`；业务/显示时区；缓存根目录 `cache_dir`
- `scheduler.*`：`mode=daily|hourly`、`daily_times`、`minute_of_hour`；仓库配置为 hourly/每小时第 0 分钟
- `cache.*`：`retain_days=30`、`max_images_per_template_per_day=3`
- `ops.api_key`：Ops API 鉴权密钥（留空则禁用 Ops 端点，处理器鉴权返回 401）
- `ops.preview_wait_timeout_sec`：预览锁等待上限（默认 2 秒；超时 503 + Retry-After）
- `metrics.enabled`：默认 false；环境变量 `MOYUREN_METRICS_ENABLED`；只控制 `/metrics` 是否对外提供，指标记录始终开启
- `feed.enabled` / `feed.max_items`：RSS 订阅源默认启用、最多 7 条（范围 1–60）；环境变量 `MOYUREN_FEED_ENABLED` / `MOYUREN_FEED_MAX_ITEMS`
- `notify.enabled` / `notify.once_per_day` / `notify.triggers` / `notify.timeout_sec`：生成成功通知总开关、每日去重、触发来源与请求超时；默认 `false` / `true` / `["scheduled"]` / `10`
- `notify.webhooks`：Webhook 列表，唯一 `name` 与 `type` 必填；`url`/`url_env` 二选一，`secret`/`secret_env` 可选且互斥，Telegram 还需 `chat_id`，`templates: null` 表示全部模板；`url_env`/`secret_env` 仅查进程环境；URL 和密钥均按敏感值处理
- `templates.*`：`default`、`dir=templates`、`hot_reload=true`、`hot_reload_interval_sec=5.0`；按 HTML 文件名/mtime/大小指纹节流发现模板
- `templates.config.*`：设备缩放/JPEG-WebP 质量与宽度、CDN、本地字体/远程字体回退、下班时间、远程资源缓存/超时/大小/重定向/主机白名单、`text_limits.*` 与 `themes.*`；模型默认值见 `TemplateRenderConfig`，仓库覆盖值见 `config.yaml`
- `network.*`：`proxy_url` 供 HTTP/Playwright 使用，`ghproxy_urls` 加速 GitHub 数据；`instances.*` 配置 60s 实例文档、额外 URL 和自动发现 allowlist
- `data_sources`：带 `type` 判别的列表：news/fun_content/crazy_thursday/holiday/stock_index/gold_price/daily_english；各项共享 `timeout_sec=10`、`enabled=true`，其余字段见对应模型和 `config.yaml`
- `logging.*`：`level=INFO`、`file`；仓库配置写入 `logs/app.log`

## 开发指引

- **修改数据处理**：`fetcher.py` → `compute.py` → 模板
- **修改节假日逻辑**：`holiday.py` → `compute.py` → 模板
- **修改趣味内容**：`fun_content.py` → `compute.py` → 模板
- **修改 KFC 功能**：`kfc.py` → `compute.py` → 模板
- **修改金价功能**：`gold_price.py` → `compute.py` → 模板
- **修改大盘指数**：`stock_index.py` → `compute.py` → 模板
- **修改每日英语**：`daily_english.py` → `compute.py` → 模板
- **修改日历/农历**：`calendar.py` → `compute.py`
- **修改配色主题**：选择规则在 `config.py` / `templates.config.themes`，农历春节期间与当前节气复用 `CalendarService`，`DomainDataAggregator` 在同一业务快照中按法定休假区间 → 当日节日 → 除夕至正月十五 → 当前节气季节 → 默认的顺序选择；`TemplateAdapter` 透传 `theme: {name, source, kind}`（kind 为 festival/season/default）。配色只在两套模板 CSS，未知主题及缺失 theme 保持默认外观；新增主题需补充文本/背景 WCAG AA 对比度测试，涨跌颜色保持红/绿。`DataComputer` 的 `themes=` 与 text_limits/work_end_time 一起由 bootstrap 及三个渲染脚本显式传入
- **添加新模板**：在 `templates/` 创建 HTML，在前 16384 个字符的 `<head>` 中填写 `moyuren:description`（去空白、最多 200 字符）和 `moyuren:viewport-width` 等 meta；默认 5 秒间隔热发现，无需重启。用 `POST /api/v1/ops/templates/preview` + Bearer 鉴权，传 `template`、`date`（可选，业务日 09:00）、`data: "sample"` 离线预览；`cached` 只读已有日级磁盘缓存，缺项用样例，不写正式图片/JSON、不通知、不计正式生成指标、不占生成锁
- **重建字体子集**：安装开发依赖后运行 `python scripts/build_fonts.py --src /path/to/fontsrc --out templates/fonts`；许可证保存在 `templates/fonts/licenses/`
- **添加 API**：在 `app/api/v1/` 创建路由 → `main.py` 注册
- **测试渲染**：`python scripts/render_once.py`
- **提交前检查**：`ruff check app tests scripts` → `mypy app` → `coverage run -m pytest -m "not e2e" --tb=short` → `coverage report --fail-under=75`；渲染烟测另见 `.github/workflows/render-smoke.yml`

## 编码规范

- 类型注解必须
- 使用 `app/core/errors.py` 中的异常类
- 通过 `config.py` 读取配置，禁止硬编码
- 使用注入的 logger，禁止 print
- 日志使用 % 惰性格式化（ruff G004）
- 注释与 docstring 统一中文；`Args:`/`Returns:`/`Raises:`/`Side Effects:` 小节标题保留英文，其下描述用中文。运行时字符串（logger、raise、HTTP 响应、断言文本）不随文档翻译改动；面向用户的文档字符串（OpenAPI `description`、argparse `help`）用中文
- 字体构建会收集 HTML 注释及 `app/services/calendar.py` docstring 的字符；这些位置的纯翻译保留原文，避免无关字体子集变化
- 修改完成后更新 README.md
- 提交代码后根据内容更新系统版本号

## 数据服务

- **图片变体**：`<stem>.webp`、`<stem>_w<width>.jpg/.webp`；LANCZOS 等比缩放并原子写入。每日 JSON 的 `image_variants` 含原始 JPEG 和成功变体；API 精确匹配格式、选择不小于请求值的最小宽度（否则最大宽度），缺失变体按需补建但不改每日 JSON。
- **详情输出**：在精简字段 date/updated/updated_at/image/variants 外返回日历、趣味内容、KFC、新闻、假日、stock_indices、gold_price、is_stale/source_date；不返回 daily_english、theme、next_rest_day/off_work，股票失败占位含 is_data_missing。
- **数据缓存与新鲜度**：节假日、趣味内容、KFC、金价、每日英语使用日级缓存/降级；股票按 TTL 缓存。生成共享一个业务时间快照，已知新闻日期不符或任一来源使用过期缓存时（股票完全失败占位除外）写入 `is_stale`、`source_date`、`stale_sources`。
- **数据源**：节假日来自 [holiday-cn](https://github.com/NateScarlet/holiday-cn)；股票来自东方财富；每日英语来自 [ECDICT](https://github.com/skywind3000/ECDICT) 与随机词 API；60s 新闻/趣味/KFC/金价可切换公共实例。
- **通知**：成功生成后按 trigger、模板过滤和每日去重规则异步推送 Webhook；通知失败与生成结果隔离，结果计入 Prometheus 指标。
- **模板与字体**：模板按目录指纹热发现；渲染优先使用 `templates/fonts/` 的本地 WOFF2，允许时再以白名单远程字体补缺字。

## 时间戳规范

API 返回格式 `YYYY/MM/DD HH:MM:SS`，模板显示通过 `format_datetime` 过滤器转为 `YYYY-MM-DD HH:MM`
