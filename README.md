# Moyuren Server

摸鱼日历图片生成服务，基于 FastAPI + Playwright。在线体验：https://api.monkeyray.net/api/v1/moyuren

![摸鱼日历预览](example/moyuren_example.jpg)

## 功能

- **生成与渲染**：定时（daily/hourly）及按需生成，多模板自动发现与热加载，Playwright 渲染；支持 JPEG、WebP 和缩小变体，部分模板或变体失败不影响其他结果。
- **日历与倒计时**：农历、节气、节日、调休感知的休息日与下班倒计时，以及周/月/年进度；内置本地字体与节日/四季主题。
- **每日内容**：新闻、趣味文案、疯狂星期四、大盘指数、金价和英语单词，数据源见下表。
- **缓存与容错**：日级缓存、股票短时缓存、后台预热、公共实例切换、过期缓存降级及自动清理；图片与详情 JSON 标记数据新鲜度。
- **访问与发布**：多格式 API、静态文件、RSS 订阅，以及 [Cloudflare Pages 静态发布](#静态发布)。
- **运维与配置**：Bearer 鉴权的生成/清理/离线数据预览、Prometheus 指标、生成成功 Webhook 通知；YAML 配置支持环境变量覆盖。

| 内容 | 数据源与回退 |
| --- | --- |
| 新闻 | [60s-api](https://60s.viki.moe)，失败后回退静态镜像；按内容日期保留较新数据 |
| 趣味文案、疯狂星期四、金价 | 60s-api，支持公共实例切换 |
| 农历、节气 | [tyme4py](https://github.com/6tail/tyme4py) |
| 节假日、补班 | [holiday-cn](https://github.com/NateScarlet/holiday-cn)，结合农历/公历节日 |
| 大盘指数 | [东方财富](https://www.eastmoney.com)；交易日历使用 [exchange_calendars](https://github.com/gerrymanoim/exchange_calendars) |
| 每日英语 | [ECDICT](https://github.com/skywind3000/ECDICT) + `random-word-api.herokuapp.com`，API 连续失败后从词典选词 |

新闻、趣味文案、疯狂星期四和金价共用 `instances` 配置，主域名不可用时切换[公共实例](https://docs.60s-api.viki.moe/7306811m0)。新闻还会回退静态镜像及 `network.ghproxy_urls`。

## 快速开始

### 环境要求

- Python >= 3.12，以及 Playwright Chromium 所需的系统库

### 本地运行

```bash
# 在仓库根目录执行（config.yaml、templates 均按工作目录解析）
pip install -r requirements.txt
playwright install chromium

# 启动服务
uvicorn app.main:app --reload
```

服务地址：http://127.0.0.1:8000

### Docker 运行

```bash
# 在仓库根目录执行，使用发布镜像
docker compose --project-directory . -f deploy/compose.yaml up -d
```

`--project-directory .` 确保读取根目录 `.env`、挂载 `./cache` 并保持项目名；`pull`、`logs`、`down` 使用相同参数。

本地构建（Compose 默认仍使用发布镜像）：

```bash
docker build -f docker/Dockerfile -t moyuren-server:local .
```

直接运行容器时，可通过 `MOYUREN_` 环境变量覆盖配置。仓库的 Compose 文件只将宿主机变量 `SERVER_PORT`、`SERVER_HOST`、`SERVER_BASE_DOMAIN`、`SCHEDULER_DAILY_TIMES`、`CACHE_RETAIN_DAYS`、`OPS_API_KEY`、`LOGGING_LEVEL` 传入容器；其他配置需修改 Compose 文件。端口映射须与监听端口一致。

镜像默认以 UID/GID 1000 运行（可通过构建参数修改）；root 启动时会递归调整 `/app/cache` 属主，再降权运行。

如遇挂载目录权限问题：

```bash
mkdir -p cache logs
sudo chown -R 1000:1000 cache logs
```

## 开发与测试

代码入口与修改约定见 [AGENTS.md](AGENTS.md)。安装依赖后运行检查：

```bash
pip install -r requirements.txt -r requirements-dev.txt
ruff check app tests scripts
mypy app
coverage run -m pytest -m "not e2e" --tb=short
coverage report --fail-under=75
```

## API

| 方法 | 路径 | 说明 |
| ---- | ---- | ---- |
| GET/HEAD | `/healthz` | 健康检查 |
| GET/HEAD | `/readyz` | 就绪检查（启动进度、当天数据与浏览器状态） |
| GET | `/metrics` | Prometheus 文本指标（`metrics.enabled=true` 时可用，否则 404） |
| GET | `/docs`、`/redoc`、`/openapi.json` | Swagger UI、ReDoc 与 OpenAPI 描述（`/metrics` 不包含在内） |
| GET | `/api/v1/moyuren` | 统一端点：元信息、详情、文本、Markdown、图片文件与图片 URL；详情含数据新鲜度 |
| GET/HEAD | `/api/v1/feed.xml` | 最近每日图片的 RSS 2.0 订阅源（`feed.enabled=true` 时可用） |
| GET | `/api/v1/templates` | 获取支持的模板列表 |
| POST | `/api/v1/ops/generate` | 手动触发图片生成（需鉴权） |
| POST | `/api/v1/ops/cache/clean` | 清理过期缓存（需鉴权） |
| POST | `/api/v1/ops/templates/preview` | 模板开发预览，直接返回 JPEG（需鉴权） |
| GET/HEAD | `/static/{filename}` | 静态图片文件 |

除 Ops 外无需 API Key。当日数据缺失时按需生成；已有 JSON 但所选模板原图缺失时仅补该模板，无法确定模板时生成全部。历史数据不补图。生成锁等待超过 5 秒或共享任务等待超过 90 秒时返回 `503 GENERATION_5002`。

### Prometheus 指标

| 指标 | 标签 | 说明 |
| ---- | ---- | ---- |
| `moyuren_generation_duration_seconds` | `result=success\|partial\|failure` | 整次共享生成任务的耗时；部分模板失败为 partial，所有模板失败为 failure |
| `moyuren_render_duration_seconds` | `template` | 每个模板的渲染耗时，包含失败尝试 |
| `moyuren_source_fetch_total` | `source`, `result=cache\|fresh\|stale\|failed` | source 为缓存命名空间；有效缓存命中、新获取、旧缓存降级、无可用数据（股票为占位数据）分别计数 |
| `moyuren_notify_total` | `webhook`, `result=success\|failure\|skipped` | 按 Webhook/模板组合计数，标签不区分模板；重试不重复计数 |
| `moyuren_instance_consecutive_failures` | `endpoint` | 实例最近报告的连续失败次数 |
| `moyuren_instance_cooling_down` | `endpoint` | 实例最近健康更新时是否冷却（0/1） |
| `moyuren_build_info` | `version` | 应用版本，值为 1 |

实例标签仅保留 `scheme://host[:port]`，同源由最近健康更新覆盖。新闻 API 数据被接受即计 fresh（不保证当天）；因日期未知或较旧而保留缓存计 stale。指标仅在当前进程累计，重启归零。

### RSS 订阅源

`GET /api/v1/feed.xml` 按日期倒序返回每日 JPEG 的 RSS 2.0 订阅源。

- `template`：省略时使用默认模板；未知模板返回 `404 API_7004`。
- `limit`：默认 `feed.max_items`，范围 1 至该上限；越界或非整数返回 `422`。
- 条目包含图片、农历、前 5 条新闻和过期提示；图片缺失仍保留条目，无数据时返回空订阅源。
- 支持 `HEAD`、`ETag`、`Last-Modified` 和条件请求，缓存为 `public, max-age=300, must-revalidate`；禁用时返回 404。

### 数据流

定时任务、启动补建或当日 API 请求通过共享任务和跨进程锁生成图片，再更新每日 JSON；API、静态文件和 RSS 读取已有产物。

启动时后台预热日级缓存并检查当天图片，不阻塞 HTTP 服务。数据获取失败时可使用过期缓存，并在图片和详情中标记新鲜度。一次生成共用业务时间快照；`updated` 是显示时区的实际写入时间（`YYYY/MM/DD HH:MM:SS`），`updated_at` 为毫秒时间戳。

### 静态发布

GitHub Actions 每 30 分钟生成静态产物并推送到 [moyuren-pages](https://github.com/monkeyray/moyuren-pages)，由 Cloudflare Pages 托管：https://moyuren.pages.dev。日期目录保留最近 `KEEP_DAYS` 天，默认 30 天。

| 路径 | 说明 |
| ---- | ---- |
| `/latest.{json,jpg,txt,md}` | 最新数据、图片、纯文本与 Markdown |
| `/history.json` | 可用日期列表 |
| `/{date}/data.{json,txt,md}`、`/{date}/moyuren.jpg` | 指定日期数据与图片 |

日期目录的变体使用 `moyuren.*`，根目录使用 `latest.*`，发布 JSON 通过 `image_variants` 列出已有变体。

### 端点详情

<details>
<summary>GET /readyz - 就绪检查</summary>

检查配置（`config`）、缓存及数据目录（`cache_dir`）、后台启动状态（`startup`）、当天数据（`today_data`）和浏览器连接（`browser`）。配置或目录缺失、启动中返回 `503 not_ready`；启动完成后均可用时返回 `200 ready`，否则返回 `200 degraded`，仍可按需生成。

</details>

<details>
<summary>GET /api/v1/moyuren - 统一端点</summary>

**查询参数**：

| 参数 | 类型 | 默认值 | 说明 |
| ---- | ---- | ------ | ---- |
| `date` | string | 业务时区今天 | 目标日期（YYYY-MM-DD），无效日期返回 `400 API_7001` |
| `encode` | string | `json` | 输出格式：`json`、`url`、`text`、`markdown`、`image` |
| `template` | string | 首个可用 | 模板名称 |
| `detail` | boolean | `false` | 是否返回详细字段（仅 encode=json） |
| `format` | `jpeg` / `webp` | `jpeg` | `encode=image/url` 的图片格式；须在 `output_formats` 中启用 |
| `width` | 正整数 | 原图宽度 | `encode=image/url` 的目标绝对像素宽度 |

`encode=json` 返回元数据，`text` / `markdown` 返回对应文本，`image` 返回 JPEG/WebP 文件，`url` 返回图片绝对 URL（纯文本）。

`format` / `width` 仅用于 image/url，否则返回 `400 API_7003`；禁用格式同样返回 400，非法格式、非正整数宽度或无效布尔值返回 `422`。不使用 `Accept` 协商。

不指定宽度时返回完整尺寸；指定时从原图、配置及该格式已记录的宽度中选择不小于请求值的最小宽度，无匹配则选最大宽度，不放大图片。已配置变体缺失时从原始 JPEG 补建；历史原图缺失返回 `404 STORAGE_4003`。变体由 `output_formats`、`output_widths` 和质量配置控制，生成失败不影响原图。

**HTTP 缓存**：

- 历史日期：`public, max-age=31536000, immutable`；当天：`public, max-age=300, must-revalidate`，支持 `ETag`、`Last-Modified` 与 `304`。
- 当天 `If-None-Match` 支持列表、`*` 和弱验证器，优先于 `If-Modified-Since`；时间按秒比较。
- 图片/URL 的 ETag 区分格式、最终宽度和文件名；仅带 `If-Modified-Since` 时返回 200，避免不同变体误命中，请用 ETag 校验。

**精简响应示例**（`encode=json`）：

```json
{
  "date": "2026-02-06",
  "updated": "2026/02/06 18:53:17",
  "updated_at": 1770375197567,
  "image": "https://api.monkeyray.net/static/moyuren_20260206_185317.jpg",
  "variants": [
    {"image": "https://api.monkeyray.net/static/moyuren_20260206_185317.jpg", "format": "jpeg", "width": 2382}
  ]
}
```

**详细响应**（`encode=json&detail=true`）增加日历、新闻、假日、趣味内容、KFC、股票、金价及新鲜度字段，完整定义见 [MoyurenDetailResponse](app/models/schemas.py)。`gold_price` 为 `{today_price,sell_price,unit}` 或 `null`；股票行情全部获取失败时 `stock_indices.is_data_missing` 为 `true`（返回占位数据）。`daily_english`、`theme`、`next_rest_day`、`off_work` 不在详情输出中。

- `is_stale`：已知新闻日期非生成业务日期，或任一源使用过期缓存；股票旧缓存计入，完全失败的占位数据不计入。
- `source_date`：新闻日期（`YYYY-MM-DD`），未知为 `null`。旧文件默认 `is_stale=false`、`source_date=null`。
- 过期时图片提示新闻来源日期或“部分数据为缓存”。

</details>

<details>
<summary>GET /api/v1/templates - 模板列表</summary>

返回模板名称、描述、当天图片 URL 和 `show_daily_english`。

无查询参数，缓存为 `public, max-age=300, must-revalidate`。`image` 取当天 JSON 映射，无映射为 `null`，不查文件或补图；`show_daily_english` 来自模板元数据，默认 `true`（主模板为 `false`）。`description` 的发现规则见配置表，模板热加载见[添加新模板](#添加新模板)。

</details>

<details>
<summary>POST /api/v1/ops/* - 运维端点（需鉴权）</summary>

仅支持 POST，须带 `Authorization: Bearer <api_key>`；`ops.api_key` 留空时禁用。处理器响应带 `Cache-Control: no-store`，鉴权失败为 `401 AUTH_6001` 并带 `WWW-Authenticate: Bearer`；GET 返回 405，无生成或清理副作用。

**POST /api/v1/ops/generate** - 手动触发图片生成

无请求体或查询参数。响应包含成功/复用文件 `results`、配置模板数 `total_templates`、当天持久化映射 `images` 和业务日期 `date`。部分失败仍返回 200，全部失败返回 `500 GENERATION_5001`。

**POST /api/v1/ops/cache/clean** - 清理过期缓存

可选 URL 查询参数 `keep_days: int | null`，默认 `cache.retain_days`；非正数返回 `400 API_7003`，非整数返回 `422`，清理失败返回 `500 OPS_8001`。成功示例：`{"data":{"deleted_files":3,"freed_bytes":12345,"oldest_kept":"2026-10-01","pruned_images":1}}`。

**POST /api/v1/ops/templates/preview** - 模板预览

JSON 请求体包含必填的已发现模板名 `template`，以及可选的 `date`、`data`：

- `date`：`YYYY-MM-DD`，省略或 `null` 为业务时区今天，按该日 09:00 计算上下文。
- `data=cached`（默认）：只读已有日级磁盘缓存，允许过期；新闻按键合并，缺失/无效来源回退样例，股票始终用样例。
- `data=sample`：使用与日期确定对应的内置数据，无需数据源服务。

成功返回 `200 image/jpeg`，不联网获取数据，不写缓存、正式图片或 JSON，不计正式指标、不通知、不占生成锁。复用正式渲染参数与资源策略，但不写远程资源缓存；远程字体仍可加载，完全离线时关闭 `remote_font_fallback`。

每进程同时预览一个模板，等待超过 `ops.preview_wait_timeout_sec`（默认 2 秒）返回 `503 GENERATION_5002`。未知模板返回 `404 API_7004`，无效日期/模式或缺字段返回 `422`，渲染失败返回 `500 GENERATION_5001`。

```bash
curl -X POST http://localhost:8000/api/v1/ops/templates/preview \
  -H "Authorization: Bearer $MOYUREN_OPS_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"template":"moyuren","data":"sample","date":"2026-10-29"}' \
  --output /tmp/moyuren-preview.jpg
```

</details>

<details>
<summary>错误响应格式</summary>

业务错误返回 `{"error":{"code","message"}}`；FastAPI 请求校验错误为 `422 {"detail":[...]}`；禁用的 metrics/feed、静态文件缺失以及未匹配的路由使用框架的 `{"detail":...}` 格式。

**常见错误码**：

| HTTP 状态码 | 错误码 | 说明 |
| ----------- | ------ | ---- |
| 400 | `API_7001` | 无效的日期格式 |
| 400 | `API_7002` | 无效的 encode 参数 |
| 400 | `API_7003` | 无效的请求参数 |
| 401 | `AUTH_6001` | 未授权（API Key 无效） |
| 404 | `API_7004` | 模板不存在 |
| 404 | `API_7005` | 数据不存在 |
| 404 | `STORAGE_4003` | 无可用图片 |
| 400 / 500 | `STORAGE_4002` | 非法图片路径 / 数据文件读取或校验失败 |
| 500 | `RENDER_3003` | 原图无法读取尺寸 |
| 500 | `GENERATION_5001` | 图片生成或预览失败 |
| 500 | `OPS_8001` | 缓存清理失败 |
| 503 | `GENERATION_5002` | 生成或预览繁忙；Retry-After 为图片 API 5 秒、Ops 生成 10 秒、预览 2 秒 |

</details>

## 配置

配置文件为 [`config.yaml`](config.yaml)，模型定义见 `app/core/config.py`。

### 配置项与环境变量

下表为模型默认值，“必填”表示无默认值；仓库 YAML 的不同取值在表中注明。顶层 `server`、`paths`、`scheduler`、`cache`、`templates`、`data_sources`、`logging` 必须提供，其余组可省略。

- **优先级**：进程环境 → 当前工作目录 `.env` → YAML → 模型默认值。
- **命名**：大小写不敏感，使用 `MOYUREN_<组>_<字段>`，仅组后的第一个 `_` 分隔层级，如 `MOYUREN_CACHE_MAX_IMAGES_PER_TEMPLATE_PER_DAY`。无前缀、未知或更深层名称会被忽略。
- **对象/列表**：使用 JSON，如 `MOYUREN_TEMPLATES_CONFIG='{"output_formats":["jpeg","webp"]}'`；也可整体覆盖 `MOYUREN_TEMPLATES`。对象合并，列表整体替换，不支持索引覆盖；子字段通过所属对象/列表设置。
- **Webhook 引用**：`url_env` / `secret_env` 可引用任意进程环境变量名，但不读取仅存在于 `.env` 的值。

| 配置项 | 模型默认值 | 环境变量 | 说明 |
| --- | --- | --- | --- |
| `server.host` | 必填；YAML 为 `0.0.0.0` | `MOYUREN_SERVER_HOST` | 仅 `docker/docker-entrypoint.sh` 读取；应用本身不使用，本地 uvicorn 由命令行决定 |
| `server.port` | 必填；YAML 为 `8000` | `MOYUREN_SERVER_PORT` | 同上，本地 uvicorn 用 `--port` 指定 |
| `server.base_domain` | 必填；YAML 为 `http://127.0.0.1:8000` | `MOYUREN_SERVER_BASE_DOMAIN` | 图片、RSS 与通知的公开 URL 前缀 |
| `paths.cache_dir` | `cache` | `MOYUREN_PATHS_CACHE_DIR` | 缓存根目录 |
| `scheduler.mode` | `daily` | `MOYUREN_SCHEDULER_MODE` | `daily` / `hourly`；仓库 YAML 使用 `hourly` |
| `scheduler.daily_times` | `["06:00"]` | `MOYUREN_SCHEDULER_DAILY_TIMES` | 业务时区时间列表；daily 模式不能为空 |
| `scheduler.minute_of_hour` | `0` | `MOYUREN_SCHEDULER_MINUTE_OF_HOUR` | 每小时触发分钟，0–59 |
| `cache.retain_days` | `30` | `MOYUREN_CACHE_RETAIN_DAYS` | 正整数，缓存保留天数 |
| `cache.max_images_per_template_per_day` | `3` | `MOYUREN_CACHE_MAX_IMAGES_PER_TEMPLATE_PER_DAY` | 正整数，每模板每天原始 JPEG 数量上限 |
| `ops.api_key` | `""` | `MOYUREN_OPS_API_KEY` | 留空则禁用 ops 端点，处理器鉴权返回 401 |
| `ops.preview_wait_timeout_sec` | `2.0` | `MOYUREN_OPS_PREVIEW_WAIT_TIMEOUT_SEC` | 有限正数，预览并发锁等待秒数 |
| `metrics.enabled` | `false` | `MOYUREN_METRICS_ENABLED` | 是否提供 `/metrics`；指标记录始终开启 |
| `feed.enabled` | `true` | `MOYUREN_FEED_ENABLED` | 是否提供 RSS；关闭时 404 |
| `feed.max_items` | `7` | `MOYUREN_FEED_MAX_ITEMS` | RSS 条目上限，1–60 |
| `notify.enabled` | `false` | `MOYUREN_NOTIFY_ENABLED` | 启用通知；还需非空 webhooks 才创建通知服务 |
| `notify.once_per_day` | `true` | `MOYUREN_NOTIFY_ONCE_PER_DAY` | 同一业务日、Webhook、模板成功后去重 |
| `notify.triggers` | `["scheduled"]` | `MOYUREN_NOTIFY_TRIGGERS` | `scheduled` / `manual` / `startup` 列表 |
| `notify.timeout_sec` | `10.0` | `MOYUREN_NOTIFY_TIMEOUT_SEC` | 正数，单次请求超时秒数 |
| `notify.webhooks` | `[]` | `MOYUREN_NOTIFY_WEBHOOKS` | Webhook 对象列表，字段见下表 |
| `logging.level` | `INFO` | `MOYUREN_LOGGING_LEVEL` | DEBUG / INFO / WARNING / ERROR / CRITICAL |
| `logging.file` | `""`；YAML 为 `logs/app.log` | `MOYUREN_LOGGING_FILE` | 空值只输出到标准输出 |
| `timezone.business` | `Asia/Shanghai` | `MOYUREN_TIMEZONE_BUSINESS` | 业务日历与调度时区；IANA 名或 `UTC+8` 形式的偏移 |
| `timezone.display` | `local` | `MOYUREN_TIMEZONE_DISPLAY` | 时间戳展示时区，额外接受 `local`（宿主本地时区） |
| `network.proxy_url` | `null` | `MOYUREN_NETWORK_PROXY_URL` | HTTPX 与 Playwright 共用；支持带认证的 HTTP 代理、无认证 SOCKS5；拒绝 SOCKS 认证 |
| `network.ghproxy_urls` | `[]` | `MOYUREN_NETWORK_GHPROXY_URLS` | GitHub 代理前缀列表；HTTP(S)，不带 query/fragment |
| `instances.list_url` | `null`；YAML 指向 60s 实例文档 | `MOYUREN_INSTANCES_LIST_URL` | Markdown 自动发现地址，null 关闭发现 |
| `instances.urls` | `null`；YAML 为 `[]`（归一化后 null） | `MOYUREN_INSTANCES_URLS` | 可信实例基地址列表，优先于发现结果 |
| `instances.allowlist` | `null` | `MOYUREN_INSTANCES_ALLOWLIST` | 仅筛选发现实例；null 不筛选，[] 全部拒绝 |
| `templates.default` | `null`；YAML 为 `moyuren` | `MOYUREN_TEMPLATES_DEFAULT` | 渲染/RSS 默认模板；未指定时退回首个模板，moyuren API 则取当日图片映射首项 |
| `templates.dir` | `templates` | `MOYUREN_TEMPLATES_DIR` | HTML 模板目录，相对进程工作目录 |
| `templates.hot_reload` | `true` | `MOYUREN_TEMPLATES_HOT_RELOAD` | 模板发现热加载 |
| `templates.hot_reload_interval_sec` | `5.0` | `MOYUREN_TEMPLATES_HOT_RELOAD_INTERVAL_SEC` | 有限正数，目录指纹检查间隔秒数 |
| `templates.config` | 下表中的渲染默认值 | `MOYUREN_TEMPLATES_CONFIG` | 全局渲染对象，可用 JSON 部分覆盖 |
| `templates.items` | `[]` | `MOYUREN_TEMPLATES_ITEMS` | 运行时发现的模板项，通常由 HTML meta 生成；序列化配置时排除 |
| `data_sources` | 必填 | `MOYUREN_DATA_SOURCES` | 下表的数据源列表，同一 type 不可重复；缺少 holiday 时自动补默认项 |

渲染字段均通过 `MOYUREN_TEMPLATES_CONFIG` JSON 覆盖：

| 配置项（`templates.config.*`） | 模型默认值 | 说明 |
| --- | --- | --- |
| `device_scale_factor` | `3` | 正整数，设备缩放因子 |
| `jpeg_quality` | `100` | JPEG 质量，1–100 |
| `output_formats` | `["jpeg"]` | jpeg / webp，不重复且必须包含 jpeg |
| `output_widths` | `[]` | 不重复的正整数，最终绝对像素宽度；只生成小于原图的宽度 |
| `webp_quality` | `85` | 整数，1–100 |
| `use_china_cdn` | `true` | 远程字体使用大陆 CDN；false 使用国际 CDN |
| `local_fonts_dir` | `templates/fonts` | 本地字体目录，相对进程工作目录 |
| `remote_font_fallback` | `true` | 是否启用 Google Fonts 缺字回退 |
| `work_end_time` | `"18:00"` | 业务时区下班时间，严格 HH:MM |
| `remote_resource_cache_enabled` | `true` | 远程资源磁盘缓存开关 |
| `remote_resource_cache_ttl_sec` | `604800` | 缓存 TTL 秒数，1–31536000 |
| `remote_resource_timeout_sec` | `5.0` | 远程请求超时秒数，>0 且 ≤60 |
| `page_load_timeout_sec` | `10.0` | DOM 加载和截图各自超时秒数，>0 且 ≤60 |
| `font_ready_timeout_sec` | `2.0` | 字体等待上限秒数，>0 且 ≤60；超时中止待加载及后续字体请求 |
| `remote_resource_max_size_kb` | `5120` | 单个资源体积上限，1–51200 KB |
| `remote_resource_max_redirects` | `5` | 整数 0–20；每跳检查白名单，0 禁止重定向 |
| `allowed_resource_hosts` | `["fonts.googleapis.com","fonts.googleapis.cn","fonts.gstatic.com","fonts.gstatic.cn"]` | 允许的资源主机及子域名；[] 拒绝远程资源 |
| `text_limits` | 见下方逐字段表 | 正整数文本长度与新闻条数限制对象 |
| `themes.enabled` | `true` | 自动主题开关 |
| `themes.festival_themes` | `{"除夕":"spring_festival","春节":"spring_festival","元宵节":"spring_festival","中秋节":"mid_autumn","国庆节":"national_day"}` | 节日名 → CSS 主题；主题名匹配 `^[a-z][a-z0-9_]*$`，{} 关闭节日主题 |
| `themes.season_themes` | `true` | 按当前节气选择 spring / summer / autumn / winter |

Webhook 子字段均经 `MOYUREN_NOTIFY_WEBHOOKS` JSON 列表设置：

| 配置项（`notify.webhooks[].*`） | 模型默认值 | 说明 |
| --- | --- | --- |
| `name` | 必填 | 非空唯一名称，供日志和指标使用 |
| `type` | 必填 | wecom / feishu / dingtalk / telegram / generic |
| `url` / `url_env` | 均为 `null`，须二选一 | HTTP(S) URL 或持有 URL 的进程环境变量名 |
| `secret` / `secret_env` | 均为 `null`，最多选一 | 飞书/钉钉/通用 JSON 可选签名密钥或环境变量名；企业微信/Telegram 不使用该签名 |
| `chat_id` | `null` | Telegram 必填，会话 ID 或频道名 |
| `templates` | `null` | 模板名列表；null 选择本次全部生成模板，[] 不发送 |

数据源子字段均经 `MOYUREN_DATA_SOURCES` JSON 列表设置；下表省略共同前缀 `data_sources[]`：

| 配置项 | 模型默认值 | 说明 |
| --- | --- | --- |
| `type` | 必填判别字段 | news / fun_content / crazy_thursday / holiday / stock_index / gold_price / daily_english；各子模型 type 默认等于其名称，列表解析仍须提供 |
| `enabled` | `true` | 所有数据源通用；holiday 仅显式 false 时禁用 |
| `timeout_sec` | `10` | 通用正整数秒数；仓库 YAML 将趣味内容、KFC、股票、金价设为 5 |
| `[type=news].url` / `.params` | 必填 / `null` | 新闻 API 地址与可选查询参数对象 |
| `[type=fun_content].endpoints` | 必填 | 非空端点列表 |
| `[type=fun_content].endpoints[].name` | 必填 | 匹配 `^[a-z][a-z0-9_]*$` 的类型名 |
| `[type=fun_content].endpoints[].url` / `.data_path` / `.display_title` | 均必填 | 非空请求地址、点分数据路径、展示标题 |
| `[type=crazy_thursday].url` / `[type=gold_price].url` | 必填 | KFC 文案 / 金价 API 地址 |
| `[type=stock_index].quote_url` | 必填 | 行情 API 地址 |
| `[type=stock_index].secids` | 必填 | 非空指数对象列表，按顺序展示；不接受 secid 字符串列表 |
| `[type=stock_index].secids[].secid` | 必填 | `<市场前缀>.<字母数字代码>` |
| `[type=stock_index].secids[].code` | `""`，校验时从 secid 派生 | 指数代码；派生值覆盖传入 code，代码不可重复 |
| `[type=stock_index].secids[].name` / `.market` | 均必填 | 显式指定展示名称 / 市场键，不再推断 |
| `[type=stock_index].market_timezones` | 必填 | 非空的市场 → 时区对象，覆盖所有指数的 market |
| `[type=stock_index].cache_ttl_sec` | `60` | 正整数，内存行情缓存 TTL 秒数 |
| `[type=daily_english].word_api_url` | `https://random-word-api.herokuapp.com/word` | 随机单词 API |
| `[type=daily_english].difficulty_range` | `[3,5]` | 两个整数，1 ≤ min ≤ max ≤ 5 |
| `[type=daily_english].max_retries` | `10` | 正整数，选词最大尝试次数 |
| `[type=daily_english].api_failure_threshold` | `3` | 正整数，API 连续失败后回退词典随机选词 |
| `[type=daily_english].backend` | SQLite 默认对象 | 当前仅支持以下 sqlite 字段 |
| `[type=daily_english].backend.type` | `sqlite` | 词典后端类型 |
| `[type=daily_english].backend.db_path` | `ecdict/stardict.db` | 相对路径完整拼接到 cache_dir，不剥离 cache/ 等前缀；绝对路径不变 |
| `[type=daily_english].backend.download_url` | `https://github.com/skywind3000/ECDICT/releases/download/1.0.28/ecdict-sqlite-28.zip` | 词典下载地址 |
| `[type=daily_english].backend.checksum_sha256` | `""` | 非空时校验下载文件 SHA-256 |

模板项字段经 `MOYUREN_TEMPLATES_ITEMS` JSON 列表设置或由模板发现生成，通常应编辑 HTML meta：

| 配置项（`templates.items[].*`） | 模型默认值 | 发现规则 / meta |
| --- | --- | --- |
| `name` / `path` | 均必填 | HTML 文件名（不含扩展名）/ 绝对路径 |
| `description` | `null` | `moyuren:description`，去空白后最多 200 字符 |
| `viewport` | 必填对象 | width / height 缺省分别为 794 / 1123 |
| `viewport.width` / `viewport.height` | `794` / `1123` | 正整数，`moyuren:viewport-width` / `moyuren:viewport-height` |
| `device_scale_factor` / `jpeg_quality` | 均为 `null` | `moyuren:device-scale-factor` / `moyuren:jpeg-quality`；发现时无 meta 则继承全局值 |
| `show_kfc` / `show_stock` / `show_daily_english` | 均为 `true` | `moyuren:show-kfc` / `moyuren:show-stock` / `moyuren:show-daily-english`；仅接受 true / false，大小写不敏感 |

#### 资源与实例白名单

- 渲染资源：仅接受小写归一化的裸主机名，按域名边界匹配该主机及子域名；远程主机须加入 `allowed_resource_hosts`。允许 `data:` / `blob:`，拒绝 `file:` 等其他协议；直接设置的 HTML 无需文档请求白名单。
- 公共实例：`instances.allowlist` 只筛选自动发现结果。裸主机名仅匹配自身，`*.example.com` 匹配各级子域名，不匹配根域或 `badexample.com`。

#### 主题与倒计时

主题优先级：当天法定休假区间 → 当天农历/公历节日 → 除夕至正月十五的 `春节` 映射 → 当前节气季节 → `default`。通过 `theme-<name>` CSS 类生效，未定义主题使用默认外观。

休息日优先依据 holiday-cn 的休假和补班安排判断，缺少数据时退回普通周末。下班倒计时使用生成时的业务时间，不实时更新。

#### 文本限制

compute 层对超长文本截断并以 `…` 结尾（计入上限），模板与持久化数据使用同一值，详情 API 字段不变。

| 字段（`templates.config.text_limits.*`） | 默认值 | 内容 |
| --- | --- | --- |
| `news_item` | 120 | 每条新闻字符数 |
| `news_items` | 15 | 新闻条数 |
| `fun_content` | 300 | 趣味内容字符数 |
| `kfc_text` | 300 | KFC 文案字符数 |
| `daily_english_definition` | 200 | 每日英语释义字符数 |
| `daily_english_translation` | 200 | 每日英语翻译字符数 |
| `holiday_name` | 20 | 节假日及节日名称字符数 |

### 生成成功通知

启用 `notify.enabled` 并配置非空 `webhooks` 后，每次实际生成成功按 Webhook/模板后台发送；共享任务的加入者或缓存复用不重复推送，失败不影响生成。触发来源为 `scheduled`（默认）、`manual`（含 Ops）、`startup`；模板筛选见配置表。

消息含日期、星期、农历和过期提示，图片 URL 为 `server.base_domain` + `/static/<filename>`，须对接收方可访问。建议用 `url_env` / `secret_env` 引用已导出的环境变量。以下为占位地址；Telegram URL 须含 Bot Token，`chat_id` 支持数字 ID 或频道名：

| 提供商 / `type` | `url_env` 对应的 URL 示例 | 推送内容与签名 |
| --- | --- | --- |
| 企业微信 / `wecom` | `https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=<key>` | `news.articles` 图文卡片，含标题、描述、链接和图片 URL |
| 飞书 / `feishu` | `https://open.feishu.cn/open-apis/bot/v2/hook/<token>` | `post` 富文本标题、文字及图片链接，不上传图片；可选时间戳/HMAC 签名 |
| 钉钉 / `dingtalk` | `https://oapi.dingtalk.com/robot/send?access_token=<token>` | `markdown` 标题、图片及文字；可选 URL 时间戳/HMAC 签名 |
| Telegram / `telegram` | `https://api.telegram.org/bot<token>` | 向 `/sendPhoto` 发送 `chat_id`、图片 URL 与说明文字 |
| 通用 JSON / `generic` | `https://hooks.example.com/moyuren` | `event: moyuren.generated`、`date`、`template`、`image_url`、`updated_at`、`is_stale`；可选秒级 `X-Moyuren-Timestamp` 与 `X-Moyuren-Signature: sha256=<HMAC>`，签名内容为 `<timestamp>.<原始请求体>`，接收方应校验时间戳以拒绝重放 |

- **重试**：网络错误、HTTP 5xx 退避后重试一次；4xx、其他非 2xx 或提供商错误响应计失败。
- **去重**：`once_per_day=true` 按业务日/Webhook 名/模板去重，成功的 `updated_at` 原子写入 `<cache_dir>/notify_state.json`，跨重启生效；失败不消耗资格。关闭后每次符合条件的实际生成都推送。
- **状态与保密**：状态缺失时从空开始，损坏/不可读时另记警告；日志及配置表示/序列化隐藏 URL、Token、签名查询和密钥。

### 调度说明

`daily` 按 `daily_times` 的每个 `HH:MM` 注册任务；`hourly` 每小时在 `minute_of_hour` 分触发并忽略 `daily_times`。时间均按 `timezone.business`，不受宿主时区影响；允许迟到 300 秒（超过即跳过），多次错过合并，每任务最多一个运行实例。

### 缓存目录结构

```text
cache/
├── data/              # YYYY-MM-DD.json，每日数据
├── images/            # 各模板原始 JPEG 与变体
├── daily/             # 新闻、趣味、KFC、金价、英语、节假日日缓存
├── holidays/          # YYYY.json，节假日原始年度数据
├── ecdict/            # 词典数据库
├── render_resources/  # 远程资源缓存（启用时）
├── notify_state.json  # Webhook 去重状态（启用时）
└── .generation.lock   # 跨进程生成锁
```

每日 JSON 格式见 [DailyDataFile](app/models/schemas.py)。保留期内的图片按模板和日期保留最新原图，变体随原图清理；JSON 引用的原图及其变体优先保留，JSON 无效或不可读时跳过该日期的同日数量裁剪。

### 配置示例

保留仓库 YAML 的数据源设置，只覆盖调度、运维密钥及变体：

```bash
export MOYUREN_SCHEDULER_MODE=daily
export MOYUREN_SCHEDULER_DAILY_TIMES='["06:00","18:00"]'
export MOYUREN_OPS_API_KEY='replace-with-your-key'
export MOYUREN_TEMPLATES_CONFIG='{"output_formats":["jpeg","webp"],"output_widths":[600,1200],"remote_font_fallback":false}'
uvicorn app.main:app --reload
```

## 添加新模板

在 `templates/` 创建文件名只含字母、数字、下划线和连字符的 `.html` 文件。在文件前 16384 个字符的 `<head>` 中填写元数据，例如：

```html
<meta name="moyuren:description" content="我的摸鱼日历模板">
<meta name="moyuren:viewport-width" content="794">
<meta name="moyuren:viewport-height" content="1123">
<meta name="moyuren:jpeg-quality" content="90">
```

使用 Jinja2，变量参考内置模板，meta 字段见配置表；通过 Ops 预览的 `sample` 模式调试，无需生成正式图片。

默认热加载：模板列表、生成和预览每隔 `hot_reload_interval_sec`（默认 5 秒）检查文件名、纳秒 mtime 与大小，变化后重新发现，失败保留上一份列表；Jinja2 下次渲染读取新内容。关闭 `hot_reload` 仅固定发现列表。开发时须绕过模板列表的客户端缓存查看更新。

## 许可证

AGPL-3.0
