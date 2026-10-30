# Moyuren API

摸鱼日历图片生成服务 | FastAPI + Playwright | Python 3.12

## 架构

外部数据源（节假日/趣味内容/KFC/金价/股票指数/交易日历）→ DataFetcher → CalendarService（农历/节气/时区）→ DataComputer → ImageRenderer（Playwright）→ JPEG → CacheDir → API 端点

## 核心模块

| 模块 | 文件 | 职责 |
|------|------|------|
| 入口 | `app/main.py` | 应用生命周期、定时任务注册 |
| 指标 | `app/core/metrics.py` | 专用 Prometheus 注册表、生成/渲染耗时、获取结果与实例健康指标 |
| 指标 API | `app/api/metrics.py` | 可选根路径 `/metrics` 文本导出 |
| 启动 | `app/core/bootstrap.py` | 服务构建、定时任务注册、启动缓存验证 |
| API | `app/api/v1/moyuren.py` | REST 端点 |
| Ops API | `app/api/v1/ops.py` | 运维端点（手动生成、缓存清理） |
| 模板 API | `app/api/v1/templates.py` | 模板列表查询 |
| RSS API | `app/api/v1/feed.py` | 最近每日图片 RSS 2.0 订阅源、条件请求与缓存 |
| API 公共工具 | `app/api/v1/common.py` | 图片 URL、ETag 与条件请求校验 |
| 服务容器 | `app/core/services.py` | AppServices 服务容器 |
| 日级缓存 | `app/services/daily_cache.py` | 日级缓存抽象基类（自动过期、降级） |
| 缓存清理 | `app/services/cache.py` | 缓存清理服务 |
| 获取 | `app/services/fetcher.py` | 异步并行 HTTP 请求（主源 + 公共实例智能切换 + 静态镜像兜底） |
| 实例路由 | `app/services/instances.py` | 解析 60s 公共实例列表文档（带 TTL 缓存）并在主源/实例间智能切换 |
| 实例配置 | `config.yaml` `instances.*` | 全局 60s 公共实例清单（news/fun_content/crazy_thursday/gold_price 共享） |
| 日历 | `app/services/calendar.py` | 农历、节气、时区管理与法定休假/补班判断（CalendarService） |
| 节假日 | `app/services/holiday.py` | 中国法定节假日数据获取与处理 |
| 趣味内容 | `app/services/fun_content.py` | 随机获取冷笑话/一言/段子（主源不可用时切换公共实例） |
| KFC | `app/services/kfc.py` | 疯狂星期四文案（周四刷新，主源不可用时切换公共实例） |
| 金价 | `app/services/gold_price.py` | 实时金价数据获取与缓存（主源不可用时切换公共实例） |
| 股票指数 | `app/services/stock_index.py` | 大盘指数实时行情（StockIndexService） |
| 每日英语 | `app/services/daily_english.py` | 每日英语单词服务（ECDICT + 随机 API） |
| 计算 | `app/services/compute.py` | 原始数据 → 模板上下文，合并假期内节日，按业务快照计算休息日/下班倒计时与数据新鲜度 |
| 渲染 | `app/services/renderer.py` | Jinja2 + Playwright 截图；通过虚拟域名提供本地 WOFF2 字体，Google Fonts 仅作缺字回退 |
| 浏览器 | `app/services/browser.py` | Playwright 浏览器生命周期管理 |
| 模板发现 | `app/services/template_discovery.py` | 自动扫描模板目录，解析 meta 标签 |
| 图片生成 | `app/services/generator.py` | 图片生成流水线（文件锁防并发、缓存管理） |
| 图片变体 | `app/services/variants.py` | Pillow 在线程中从 JPEG 原子生成 WebP/缩小图片，失败隔离；API 可按需补建 |
| 调度 | `app/core/scheduler.py` | APScheduler 定时任务 |
| 配置 | `app/core/config.py` | YAML + 环境变量 |

## 快速启动

本地：`pip install -r requirements.txt && playwright install chromium && uvicorn app.main:app --reload`
Docker：`docker-compose up -d`
优先使用：`~/miniconda3/bin/python`

## API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/healthz` | 健康检查 |
| GET | `/readyz` | 就绪检查（含依赖验证） |
| GET | `/metrics` | Prometheus 文本指标；`metrics.enabled` 默认 false，未启用时返回 404 |
| GET | `/api/v1/moyuren` | 获取最新图片元数据（缓存不存在时自动生成）；`detail=true` 返回 `is_stale` / `source_date` |
| GET/HEAD | `/api/v1/feed.xml` | 最近每日图片 RSS 2.0 订阅源；支持模板、条目上限与条件请求 |
| GET | `/api/v1/templates` | 获取可用模板列表 |
| POST | `/api/v1/ops/generate` | 手动触发图片生成（需鉴权） |
| POST | `/api/v1/ops/cache/clean` | 清理过期缓存（需鉴权） |
| GET | `/static/{filename}` | 静态图片 |

## 配置

主配置：`config.yaml`，支持 `MOYUREN_` 前缀环境变量覆盖（`MOYUREN_SERVER_HOST`、`MOYUREN_SERVER_PORT` 等）

关键配置项：
- `server.*`：服务器配置（host/port/base_domain）
- `timezone.*`：时区配置（business/display）
- `paths.*`：路径配置（cache_dir）
- `scheduler.daily_times`：每日生成时间（如 `["06:00", "18:00"]`）
- `cache.retain_days`：缓存保留天数（默认 30 天）
- `ops.api_key`：Ops API 鉴权密钥（留空则禁用 Ops 端点，所有请求返回 401）
- `metrics.enabled`：默认 false；环境变量 `MOYUREN_METRICS_ENABLED`；只控制 `/metrics` 是否对外提供，指标记录始终开启
- `feed.enabled` / `feed.max_items`：RSS 订阅源默认启用、最多 7 条（范围 1–60）；环境变量 `MOYUREN_FEED_ENABLED` / `MOYUREN_FEED_MAX_ITEMS`
- `templates.dir`：模板目录路径（默认 `templates`，自动扫描 `*.html`）
- `templates.config.local_fonts_dir`：本地 WOFF2 字体目录（默认 `templates/fonts`；与 `templates.dir` 一样，相对路径基于进程工作目录，通常为项目根目录）
- `templates.config.remote_font_fallback`：是否启用 Google Fonts 缺字回退（默认 true，离线部署可关闭）
- `templates.config.output_formats`：默认 `["jpeg"]`，可启用 `webp`；必须包含 `jpeg` 且不可重复
- `templates.config.output_widths`：默认 `[]`，唯一正整数的最终绝对像素宽度；大于等于原图宽度时跳过
- `templates.config.webp_quality`：默认 85，整数 1–100；缩小 JPEG 沿用 `jpeg_quality`
- `templates.config.themes`：自动主题配置，`enabled=true`、`season_themes=true`；`festival_themes` 默认将除夕/春节/元宵节映射到 `spring_festival`、中秋节到 `mid_autumn`、国庆节到 `national_day`，主题名须匹配 `^[a-z][a-z0-9_]*$`
- `logging.*`：日志配置
- `network.ghproxy_urls`：GitHub 代理 URL 列表（加速节假日数据/ECDICT 下载）
- `data_sources`：数据源配置统一在此列表中管理（news/fun_content/crazy_thursday/holiday/stock_index/gold_price/daily_english）
- `instances.*`：全局 60s 公共实例配置（`list_url` 官方实例列表文档、`urls` 额外实例），被 news/fun_content/crazy_thursday/gold_price 共享

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
- **添加新模板**：在 `templates/` 目录创建 HTML 文件，在 `<head>` 中添加 `<meta name="moyuren:viewport-width" content="794">` 等 meta 标签，重启后自动发现
- **重建字体子集**：安装开发依赖后运行 `python scripts/build_fonts.py --src /path/to/fontsrc --out templates/fonts`；许可证保存在 `templates/fonts/licenses/`
- **添加 API**：在 `app/api/v1/` 创建路由 → `main.py` 注册
- **测试渲染**：`python scripts/render_once.py`

## 编码规范

- 类型注解必须
- 使用 `app/core/errors.py` 中的异常类
- 通过 `config.py` 读取配置，禁止硬编码
- 使用注入的 logger，禁止 print
- 日志使用 % 惰性格式化（ruff G004）
- 修改完成后更新 README.md
- 提交代码后根据内容更新系统版本号

## 数据服务

图片变体：`<stem>.webp`、`<stem>_w<width>.jpg/.webp`；LANCZOS 保留宽高比，移除派生文件元数据，临时文件 + `os.replace` 原子写入。每日 JSON 可选 `image_variants: {模板: [{filename, format: "jpeg|webp", width: 正整数}]}`，含原始 JPEG 和成功变体；旧文件兼容，schema_version 保持 1。GET `/api/v1/moyuren?encode=image|url&format=webp&width=600` 按精确格式和最小不低于目标的宽度选择，无匹配则用最大宽度，省略宽度用完整尺寸；禁用格式返回 400/API_7003。缺失变体在线程中补建而不改 JSON，缺失原图沿用自动恢复；ETag 包含变体身份，JSON/详情公开带绝对 `image` URL 的 `variants`。缓存上限只统计原始 JPEG，删除时连带变体和字节数，保护数据引用原图的所有变体并清理孤立变体。静态发布及 render_once 同步支持变体。

- **节假日服务**：数据源 [holiday-cn](https://github.com/NateScarlet/holiday-cn)，缓存目录 `cache/holidays/`
- **金价服务**：数据源外部金价 API，日级缓存自动过期
- **股票指数服务**：数据源东方财富 API，实时行情数据
- **每日英语服务**：数据源 [ECDICT](https://github.com/skywind3000/ECDICT) + 随机词 API，日级缓存自动过期

## 时间戳规范

API 返回格式 `YYYY/MM/DD HH:MM:SS`，模板显示通过 `format_datetime` 过滤器转为 `YYYY-MM-DD HH:MM`

数据新鲜度：每次生成使用同一个业务时间快照；新闻日期不同于业务日期，或任一数据源降级返回过期缓存（含股票 `is_stale`），即标记 `data_freshness.is_stale`。新闻日期规范化为 `source_date`，无法解析时为 null；未知日期本身不触发过期。数据文件额外保存 `stale_sources`，旧文件仍可读取，详情 API 默认 false/null。两个模板只在过期时显示一行轻量提示，缺少新字段的旧上下文仍可渲染。
