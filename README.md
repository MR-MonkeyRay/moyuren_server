# Moyuren Server

摸鱼日历图片生成服务 | FastAPI + Playwright

## 预览

![摸鱼日历预览](example/moyuren_example.jpg)

## api体验

```bash
https://api.monkeyray.net/api/v1/moyuren
```

## 功能

- 定时生成摸鱼日历图片（支持 daily/hourly 模式）
- 按需生成：启动时或请求当天数据时若无可用图片则自动生成；当天 JSON 存在但所选模板图片丢失时仅尝试重新生成该模板（无法确定模板时生成全部），历史数据不触发补图
- 智能缓存管理：
  - 日级缓存：数据源独立缓存，次日自动过期
  - 日级缓存基类与新闻缓存：文件读写在线程中执行，同一服务实例的并发读取串行检查缓存，避免重复抓取；强制刷新仍逐次执行，失败时保留过期缓存降级。新闻仍按内容日期判断新鲜度
  - 启动预热：应用启动时并行预热所有缓存
  - 混合更新策略：缓存过期时后台异步刷新（快速启动），无缓存时同步生成
  - 降级策略：网络失败时返回过期缓存
  - 自动清理过期图片文件
- 60s-api 数据源公共实例智能切换（新闻、趣味内容、疯狂星期四、金价）
  - 60s 主域名对数据中心/海外出口 IP 会返回 Cloudflare 403，此时自动切换到
    [公共实例](https://docs.60s-api.viki.moe/7306811m0)：实例清单由全局配置
    `instances.list_url` 指向的文档页自动解析（6 小时缓存），四个数据源共享，并按健康度智能切换——
    优先复用上次成功的实例（粘性），连续失败 2 次的实例降级到最后尝试（10 分钟冷却），单次最多尝试 4 个端点
- 数据源请求与公共实例发现复用应用的 HTTP 客户端和连接池，各自保留请求超时；未注入客户端的独立脚本仍可运行。ECDICT 大文件下载使用独立客户端与长超时
- 60 秒读懂世界新闻
  - 数据源：[60s-api](https://60s.viki.moe)
  - 主源与公共实例均不可用时，继续回退到 60s 静态镜像
    （`cdn.jsdmirror.com` → `60s-static.viki.moe` → GitHub raw → `network.ghproxy_urls` 代理前缀），
    避免新闻长期停留在旧日期
  - 错误信封、空新闻与解析失败均触发后续数据源回退；API 未更新到今天时，按新闻日期保留 API 与本地缓存中较新的数据
- 农历信息与节气（干支年、生肖、二十四节气）
  - 数据源：[tyme4py](https://github.com/6tail/tyme4py)
- 节日倒计时整合（法定假日 + 农历/公历节日）
  - 数据源：[holiday-cn](https://github.com/NateScarlet/holiday-cn)
  - 农历节日按农历年份跨年查询；同名法定假日保留最近一次，普通节日不标记为放假，补班单独展示
  - 年度数据全部获取失败时保留过期聚合缓存；原始年度缓存校验数据格式并原子写入，避免无效响应或写入中断破坏缓存
- 趣味内容随机展示（冷笑话、一言、段子、摸鱼语录）
  - 数据源：[60s-api](https://60s.viki.moe)，支持公共实例智能切换
- 疯狂星期四：每周四自动展示 KFC 文案
  - 数据源：[60s-api](https://60s.viki.moe)，支持公共实例智能切换
- 大盘指数实时行情（上证、深证、创业板、恒生、道琼斯）
  - 数据源：[东方财富](https://www.eastmoney.com)
  - 交易日历：[exchange_calendars](https://github.com/gerrymanoim/exchange_calendars)
- 实时金价查询（人民币/美元）
  - 数据源：[60s-api](https://60s.viki.moe)，支持公共实例智能切换
- 每日英语单词（ECDICT 词典 + 随机 API）
  - 数据源：[ECDICT](https://github.com/skywind3000/ECDICT)、[60s-api](https://60s.viki.moe)
- 周/月/年进度百分比计算
- 多模板渲染：自动发现模板目录中的 HTML 文件，通过 meta 标签自描述渲染参数，错误隔离确保部分失败不影响其他模板
- Playwright 高质量浏览器渲染
  - Chromium 崩溃或断连后自动重启，创建页面遇到浏览器关闭错误时重试一次
- 自动清理过期缓存
- RESTful API + 静态文件服务
- Prometheus 可观测性：生成/模板渲染耗时、数据源获取结果与实例健康指标始终记录；`metrics.enabled` 默认关闭，仅控制 `/metrics` 端点是否对外提供
- 数据新鲜度：新闻日期与本次生成的业务日期不一致，或任一数据源使用过期缓存时，图片显示轻量提示；详情 JSON 返回 `is_stale` 和 `source_date`，旧数据文件仍兼容
- 静态发布到 Cloudflare Pages（GitHub Actions 定时推送）
- YAML 配置 + 环境变量覆盖

## 快速开始

### 环境要求

- Python >= 3.12（项目使用了 3.12+ 的类型注解语法如 `type | None`）

### 本地运行

```bash
# 安装依赖
pip install -r requirements.txt
playwright install chromium

# 启动服务
uvicorn app.main:app --reload
```

服务地址：http://127.0.0.1:8000

### Docker 运行

```bash
docker-compose up -d
```

如遇权限问题：

```bash
mkdir -p cache logs
sudo chown -R 1000:1000 cache logs
```

## 依赖管理

`requirements.txt` 和 `requirements-dev.txt` 保留依赖最低版本，并限制在下一个主版本之前（Ruff 和 mypy 按次版本限制）。`.github/dependabot.yml` 配置 Dependabot 每周向 `develop` 提交 Python 依赖、GitHub Actions 和 Docker 基础镜像更新；Python 次版本/补丁更新合并为一个 PR，主版本升级单独评审。

## 开发与测试

CI 在运行测试前执行 Ruff 代码检查（含日志惰性格式化规则 G004）和 mypy 类型检查。本地安装开发依赖后可运行：

```bash
ruff check app tests scripts
mypy app
pytest -m "not e2e"
```

## API

| 方法 | 路径 | 说明 |
| ---- | ---- | ---- |
| GET | `/healthz` | 健康检查 |
| GET/HEAD | `/readyz` | 就绪检查（启动进度、当天数据与浏览器状态） |
| GET | `/metrics` | Prometheus 文本指标（`metrics.enabled=true` 时可用，否则 404） |
| GET | `/api/v1/moyuren` | 统一端点：图片元信息/详情/文本/Markdown/图片；详情包含数据新鲜度 |
| GET | `/api/v1/templates` | 获取支持的模板列表 |
| POST | `/api/v1/ops/generate` | 手动触发图片生成（需鉴权） |
| POST | `/api/v1/ops/cache/clean` | 清理过期缓存（需鉴权） |
| GET | `/static/{filename}` | 静态图片文件 |

> 注：当无可用图片时，API 会自动触发按需生成；同一进程内的并发请求会等待当前生成任务，复用结果或依次生成未覆盖的模板。仅当其他进程持有生成锁或等待超时（90 秒）时返回 `503`，并附带 `Retry-After` 响应头（图片 API 为 5 秒，Ops 为 10 秒）。Ops 端点需要 `Authorization: Bearer <api_key>` 鉴权。Ops 的 GET 请求仍兼容，但已弃用；响应包含 `Deprecation: @1793318400`，同一 URL 的 POST 请求为后继版本。

启用 `metrics.enabled` 后，`GET /metrics` 导出专用注册表中的以下指标（不出现在 OpenAPI 中）：

| 指标 | 标签 | 说明 |
| ---- | ---- | ---- |
| `moyuren_generation_duration_seconds` | `result=success\|partial\|failure` | 整次共享生成任务的耗时；部分模板失败为 partial，所有模板失败为 failure |
| `moyuren_render_duration_seconds` | `template` | 每个模板的渲染耗时，包含失败尝试 |
| `moyuren_source_fetch_total` | `source`, `result=cache\|fresh\|stale\|failed` | source 为缓存命名空间；有效缓存命中、新获取、旧缓存降级、无可用数据（股票为占位数据）分别计数 |
| `moyuren_instance_consecutive_failures` | `endpoint` | 实例最近报告的连续失败次数 |
| `moyuren_instance_cooling_down` | `endpoint` | 实例最近健康更新时是否冷却（0/1） |
| `moyuren_build_info` | `version` | 应用版本，值为 1 |

实例标签仅保留 `scheme://host[:port]`，移除凭据、路径与查询参数；同一源地址由最近一次健康更新覆盖。新闻 API 数据被接受时计为 fresh，即使内容日期并非当天；因 API 日期未知或较旧而保留旧缓存时计为 stale。指标仅保存在当前进程内，重启后重新累计。

### 静态发布

通过 GitHub Actions 每 30 分钟自动生成摸鱼日历图片及结构化数据，推送到独立仓库 [moyuren-pages](https://github.com/monkeyray/moyuren-pages)，由 Cloudflare Pages 托管。

**静态站点地址**：https://moyuren.pages.dev

**可用格式**：

| 路径 | 说明 |
| ---- | ---- |
| `/latest.json` | 最新数据（JSON） |
| `/latest.jpg` | 最新图片 |
| `/latest.txt` | 最新纯文本 |
| `/latest.md` | 最新 Markdown |
| `/history.json` | 可用日期列表 |
| `/{date}/data.json` | 指定日期数据 |
| `/{date}/moyuren.jpg` | 指定日期图片 |
| `/{date}/data.txt` | 指定日期纯文本 |
| `/{date}/data.md` | 指定日期 Markdown |

**相关文件**：
- `scripts/publish_static.py` — 静态产物生成脚本
- `.github/workflows/publish-static.yml` — 定时发布 workflow

### 端点详情

<details>
<summary>GET /healthz - 健康检查</summary>

返回服务健康状态，支持 GET 和 HEAD 方法。

**响应示例**：

```json
{
  "status": "ok"
}
```

</details>

<details>
<summary>GET /readyz - 就绪检查</summary>

验证服务是否可以处理请求，支持 GET 和 HEAD 方法。启动阶段的网络缓存预热和初始图片生成在后台依次执行，不阻塞 HTTP 服务启动；浏览器预热由单独的后台任务执行。

检查项包括配置是否已加载（`config`）、缓存与数据目录是否存在（`cache_dir`）、后台启动任务状态（`startup`）、当天业务日期的数据文件是否存在（`today_data`）以及浏览器是否已连接（`browser`）。配置或缓存目录缺失、或后台启动任务仍为 `starting` 时返回 HTTP `503` 和 `not_ready`。启动任务结束后返回 HTTP `200`：当天数据与浏览器都可用时状态为 `ready`，否则为 `degraded`，API 仍可通过按需生成提供服务。

**响应示例**：

```json
{
  "status": "ready",
  "checks": {
    "config": true,
    "cache_dir": true,
    "startup": "ready",
    "today_data": true,
    "browser": true
  }
}
```

</details>

<details>
<summary>GET /api/v1/moyuren - 统一端点</summary>

获取摸鱼日历数据，支持多种输出格式和查询参数。

**查询参数**：

| 参数 | 类型 | 默认值 | 说明 |
| ---- | ---- | ------ | ---- |
| `date` | string | 今天 | 目标日期 (YYYY-MM-DD) |
| `encode` | string | `json` | 输出格式：`json`、`url`、`text`、`markdown`、`image` |
| `template` | string | 首个可用 | 模板名称 |
| `detail` | boolean | `false` | 是否返回详细字段（仅 encode=json） |
| `format` | `jpeg` / `webp` | `jpeg` | `encode=image/url` 的图片格式；须在 `output_formats` 中启用 |
| `width` | 正整数 | 原图宽度 | `encode=image/url` 的目标绝对像素宽度 |

**输出格式**：

- `encode=json`：JSON 元数据（精简或详细）
- `encode=text`：纯文本格式
- `encode=markdown`：Markdown 格式
- `encode=image`：直接返回 JPEG 或 WebP 图片文件，使用对应的 `image/jpeg` / `image/webp`
- `encode=url`：返回所选图片的绝对 URL（纯文本）

图片选择使用指定格式；不指定 `width` 时返回完整尺寸。指定宽度时，从该格式的原图及已配置缩小尺寸中选择大于等于请求值的最小宽度，全部不足时选择最大宽度。宽度是最终图片的绝对像素，与 `device_scale_factor` 无关，不放大图片。禁用格式返回 `400 API_7003`。已配置变体缺失时，从原始 JPEG 在线程中原子补建，GET 不修改数据 JSON；原始 JPEG 缺失时沿用按需生成和 `503 Retry-After` 恢复机制。JSON 精简及详情响应均增加 `variants: [{image: "绝对 URL", format: "jpeg|webp", width: 600}]`，旧文件默认为空列表。

每次 JPEG 渲染成功后生成变体，单个变体失败仅记录警告；数据文件仅记录成功存在的文件。命名为 `<stem>.webp`、`<stem>_w<width>.jpg` 和 `<stem>_w<width>.webp`。Pillow 使用 LANCZOS 缩小、保留宽高比、移除派生文件元数据，以临时文件和 `os.replace` 原子写入；缩小 JPEG 使用 `jpeg_quality`，WebP 使用 `webp_quality`。

**HTTP 缓存**：

- 历史日期（< 今天）：`Cache-Control: public, max-age=31536000, immutable`
- 当天数据：`ETag` + `Last-Modified`，支持 `304 Not Modified`。`If-None-Match` 支持逗号分隔列表、`*` 和弱验证器比较；该请求头存在时优先处理并忽略 `If-Modified-Since`。`Last-Modified` 按秒精度比较，回传服务端返回值可正确命中缓存

图片变体的 ETag 包含格式、最终宽度和文件名身份；图片及 URL 响应的 `If-Modified-Since` 单独出现时保守返回 200，请使用 ETag 进行 304 校验，避免不同变体共享生成时间导致误命中。参数显式选择格式，不使用 `Accept` 内容协商。

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

**详细响应**（`encode=json&detail=true`）包含精简响应的所有字段，以及：weekday、lunar_date、fun_content、is_crazy_thursday、kfc_content、date_info、weekend、solar_term、guide、news_list、news_meta、holidays、kfc_content_full、stock_indices、is_stale、source_date。

`is_stale` 为新闻日期不等于生成时业务日期，或任一源使用过期缓存的标志；股票行情的 `is_stale` 也计入。`source_date` 为规范化的新闻日期（`YYYY-MM-DD`），未知时为 `null`。旧文件的详情默认 `is_stale=false`、`source_date=null`。模板仅在过期时显示提示：新闻非当日时显示“数据日期 YYYY-MM-DD（非当日）”，其余情况（新闻为当日但其他源使用缓存）显示“部分数据为缓存”。生成日期、写入时间与数据来源日期分别保留。

详细字段说明参见 [Pydantic 模型](app/models/schemas.py) 中的 `MoyurenDetailResponse`。

</details>

<details>
<summary>GET /api/v1/templates - 模板列表</summary>

获取支持的模板列表及当前图片 URL。

**响应示例**：

```json
{
  "data": [
    {
      "name": "moyuren",
      "description": "摸鱼日历moyuren模板",
      "image": "https://api.monkeyray.net/static/moyuren_20260210_072232.jpg"
    },
    {
      "name": "moyuren_cute",
      "description": "摸鱼日历moyuren_cute模板",
      "image": "https://api.monkeyray.net/static/moyuren_cute_20260210_072232.jpg"
    }
  ]
}
```

**缓存**：`Cache-Control: public, max-age=300, must-revalidate`

</details>

<details>
<summary>POST /api/v1/ops/* - 运维端点（需鉴权）</summary>

所有 Ops 端点需要 `Authorization: Bearer <api_key>` 请求头。GET 请求仍兼容，但已弃用；包括错误响应在内均返回 `Deprecation: @1793318400`，同一 URL 的 POST 请求为后继版本。

**POST /api/v1/ops/generate** - 手动触发图片生成

成功响应包含所有模板的生成结果：
```json
{
  "data": {
    "date": "2026-02-10",
    "results": {
      "moyuren": "moyuren_20260210_072232.jpg",
      "moyuren_cute": "moyuren_cute_20260210_072232.jpg"
    },
    "total_templates": 2,
    "images": {
      "moyuren": "moyuren_20260210_072232.jpg",
      "moyuren_cute": "moyuren_cute_20260210_072232.jpg"
    }
  }
}
```

**POST /api/v1/ops/cache/clean** - 清理过期缓存
- 可选查询参数：`keep_days`（保留最近 N 天，POST 请求也通过 URL 查询参数传递）

**鉴权失败响应**（401）：

```json
{
  "error": {
    "code": "AUTH_6001",
    "message": "无效的 API Key"
  }
}
```

</details>

<details>
<summary>错误响应格式</summary>

所有 API 端点在发生错误时返回统一的错误响应格式。

**响应示例**：

```json
{
  "error": {
    "code": "STORAGE_4003",
    "message": "No image available"
  }
}
```

**常见错误码**：

| HTTP 状态码 | 错误码 | 说明 |
| ----------- | ------ | ---- |
| 400 | `API_7001` | 无效的日期格式 |
| 400 | `API_7002` | 无效的 encode 参数 |
| 400 | `API_7003` | 无效的请求参数 |
| 401 | `AUTH_6001` | 未授权（API Key 无效） |
| 404 | `API_7004` | 数据不存在 |
| 404 | `API_7005` | 模板不存在 |
| 404 | `STORAGE_4003` | 无可用图片 |
| 500 | `GENERATION_5001` | 图片生成失败 |
| 503 | `GENERATION_5002` | 其他进程正在生成或等待超时（Retry-After: 5） |

</details>

## 配置

配置文件：`config.yaml`

### 主要配置项

| 配置项 | 环境变量 | 说明 |
| ------ | -------- | ---- |
| `server.host` | `MOYUREN_SERVER_HOST` | 监听地址 |
| `server.port` | `MOYUREN_SERVER_PORT` | 服务端口 |
| `server.base_domain` | `MOYUREN_SERVER_BASE_DOMAIN` | 图片 URL 前缀 |
| `paths.cache_dir` | `MOYUREN_PATHS_CACHE_DIR` | 缓存根目录（默认 `cache`） |
| `scheduler.mode` | `MOYUREN_SCHEDULER_MODE` | 调度模式（`daily` 或 `hourly`） |
| `scheduler.daily_times` | `MOYUREN_SCHEDULER_DAILY_TIMES` | 生成时间（JSON 数组，如 `'["06:00","18:00"]'`） |
| `scheduler.minute_of_hour` | `MOYUREN_SCHEDULER_MINUTE_OF_HOUR` | 每小时模式下的触发分钟（0-59） |
| `templates.config.device_scale_factor` | - | 缩放因子（默认 3） |
| `templates.config.jpeg_quality` | - | JPEG 质量（1-100，默认 100） |
| `templates.config.output_formats` | - | 默认 `["jpeg"]`；允许 `jpeg`、`webp`，必须包含 `jpeg` 且不可重复 |
| `templates.config.output_widths` | - | 默认 `[]`；额外最终像素宽度，须为唯一正整数；大于等于原图宽度时跳过并记录 debug 日志 |
| `templates.config.webp_quality` | - | WebP 质量（整数 1–100，默认 85） |
| `templates.config.use_china_cdn` | - | 字体 CDN 开关（true: 大陆 CDN, false: 国际 CDN） |
| `templates.config.local_fonts_dir` | - | 本地 WOFF2 字体目录，默认 `templates/fonts`；与 `templates.dir` 一样，相对路径基于进程工作目录解析（通常为项目根目录） |
| `templates.config.remote_font_fallback` | - | 是否加载 Google Fonts 作为本地子集的缺字回退，默认 `true`；离线部署可设为 `false` |
| `templates.config.work_end_time` | - | 业务时区下班时间，默认 `"18:00"`，严格 `HH:MM`（00:00–23:59）；仅工作日显示下班倒计时 |
| `templates.config.themes.enabled` | - | 自动主题总开关，默认 `true`；关闭后使用 `default` |
| `templates.config.themes.festival_themes` | - | 节日名 → CSS 主题名；默认 `{"除夕": "spring_festival", "春节": "spring_festival", "元宵节": "spring_festival", "中秋节": "mid_autumn", "国庆节": "national_day"}`；主题名须匹配 `^[a-z][a-z0-9_]*$`，`{}` 关闭节日主题 |
| `templates.config.themes.season_themes` | - | 默认 `true`；按当前节气区间选择 `spring` / `summer` / `autumn` / `winter`，关闭后无节日匹配时使用 `default` |
| `templates.config.allowed_resource_hosts` | - | 渲染资源主机白名单，默认 `["fonts.googleapis.com", "fonts.googleapis.cn", "fonts.gstatic.com", "fonts.gstatic.cn"]`；仅允许这些主机及其子域名的 HTTP(S) 请求；`[]` 拒绝所有远程资源 |
| `templates.config.remote_resource_max_redirects` | - | 资源请求的重定向次数上限，默认 5（整数 0–20；0 禁止重定向）；每跳检查白名单，允许白名单内跨主机及 HTTP→HTTPS 重定向 |
| `templates.config.text_limits` | - | 外部文本及新闻条数上限，所有值必须为正整数；各字段与默认值见下表 |
| `templates.config.page_load_timeout_sec` | - | 浏览器 DOM 加载和截图各自的超时（秒，默认 10） |
| `templates.config.font_ready_timeout_sec` | - | 字体 readiness 等待上限（秒，默认 2）；超时后中止待加载及后续字体请求，使用回退字体，关闭远程资源缓存时同样生效 |
| `templates.items[].viewport` | - | 各模板视口尺寸（width/height） |
| `cache.retain_days` | `MOYUREN_CACHE_RETAIN_DAYS` | 缓存保留天数（默认 30） |
| `cache.max_images_per_template_per_day` | `MOYUREN_CACHE_MAX_IMAGES_PER_TEMPLATE_PER_DAY` | 每个模板每天保留的最大图片数（默认 3） |
| `ops.api_key` | `MOYUREN_OPS_API_KEY` | 运维 API Key（留空则禁用 ops 端点，所有请求返回 401） |
| `metrics.enabled` | `MOYUREN_METRICS_ENABLED` | 是否对外提供根路径 `/metrics`，默认 `false`；修改后重启应用，指标记录始终开启 |
| `logging.level` | `MOYUREN_LOGGING_LEVEL` | 日志级别 |
| `logging.file` | `MOYUREN_LOGGING_FILE` | 日志文件路径（空字符串表示只输出到标准输出） |
| `timezone.business` | `MOYUREN_TIMEZONE_BUSINESS` | 业务时区（节假日/节气/周末判断） |
| `timezone.display` | `MOYUREN_TIMEZONE_DISPLAY` | 显示时区（图片时间戳、API 响应时间；支持 `local`） |
| `network.proxy_url` | `MOYUREN_NETWORK_PROXY_URL` | 全局出站代理 URL，默认空；支持 `http://`（可包含账号密码）和 `socks5://`（仅限无认证）；同时用于应用和脚本的 HTTPX 请求及 Playwright 浏览器启动。Chromium 不支持 SOCKS 认证，此类配置会在启动时被拒绝；需要认证时请使用 HTTP 代理，或通过 gost/privoxy 等本地转发器连接带认证的上游代理 |
| `network.ghproxy_urls` | `MOYUREN_NETWORK_GHPROXY_URLS` | GitHub 代理 URL 列表（JSON 数组，用于加速节假日数据、ECDICT 下载和新闻静态镜像回退） |
| `data_sources` | - | 外部数据源配置列表（新闻、趣味内容等） |
| `data_sources[].type` | - | 数据源类型（news/fun_content/crazy_thursday/holiday/stock_index/gold_price/daily_english） |
| `data_sources[type=holiday].enabled` | `true` | 节假日服务开关；未配置 holiday 项时默认启用，显式设为 `false` 时不创建、预热或调用节假日服务 |
| `data_sources[type=stock_index].secids` | - | 指数对象列表（`secid`、`name`、`market`）；列表顺序即显示顺序，名称以配置为准；旧字符串格式仍兼容 |
| `data_sources[type=daily_english].backend.db_path` | - | ECDICT 数据库路径，默认 `ecdict/stardict.db`；相对路径基于 `paths.cache_dir`，兼容旧配置 `cache/ecdict/stardict.db`（去掉开头的 `cache/` 后解析），绝对路径保持不变 |
| `instances.list_url` | `MOYUREN_INSTANCES_LIST_URL` | 60s 公共实例列表文档地址（Markdown），由 news/fun_content/crazy_thursday/gold_price 共享；`null` 关闭自动发现 |
| `instances.urls` | `MOYUREN_INSTANCES_URLS` | 额外/兜底的实例基地址 JSON 数组（可选，优先于自动发现结果；端点路径沿用各数据源的 `url`） |
| `instances.allowlist` | `MOYUREN_INSTANCES_ALLOWLIST` | 自动发现实例的主机白名单，默认 `null`（不筛选）；`[]` 拒绝所有发现实例；显式 `instances.urls` 始终受信任 |
| `templates.default` | - | 默认模板名 |
| `templates.dir` | - | 模板目录（默认 `templates`，自动扫描 HTML 文件） |

环境变量按进程环境、当前工作目录的 `.env`、`config.yaml` 的顺序覆盖。进程环境和 `.env` 内均以 `MOYUREN_` 前缀名称为准；同一来源同时设置新旧名称时，新名称优先。已知配置项的旧无前缀名称仍可用，但已弃用，使用时会记录警告；无关或未知名称会被忽略。

渲染资源白名单只接受裸主机名（不含协议、端口或路径），统一转为小写，并按完整域名边界匹配子域名。`data:`、`blob:` 内嵌资源继续可用；`file:` 和其他协议被拒绝。`page.set_content` 直接设置文档，不需要放行文档网络请求。预连接链接可以被阻止；自定义模板需要远程资源时须将主机加入白名单。

`instances.allowlist` 接受裸主机名或 `*.suffix`，统一转为小写。裸主机名仅匹配该主机；`*.example.com` 匹配 `api.example.com` 和更深的子域名，**不匹配** `example.com` 或 `badexample.com`。白名单仅筛选社区文档自动发现的实例。

`templates.config.text_limits` 在 compute 层截断文本，超长内容以 `…` 结尾，省略号计入字符上限；短文本保持原样。模板上下文与持久化数据使用相同的截断值，详情 API 字段保持不变。

两套模板支持节日/节气季节配色，共用本次生成的业务时间快照。主题优先级：包含当天的法定休假区间（`is_off_day` 为真且原始名称命中映射）→ 当天农历/公历节日 → 除夕至正月十五（含首尾，使用配置中的 `春节` 映射）→ 当前节气季节 → `default`。区间首尾均包含，补班不按休假区间匹配；节日名称匹配在展示截断之前完成。季节按立春、立夏、立秋、立冬当天切换，`source` 是当前节气而非倒计时中的下个节气。

模板上下文新增 `theme: {"name": str, "source": str | null, "kind": "festival" | "season" | "default"}`；默认值为 `{"name": "default", "source": null, "kind": "default"}`。`enabled: false` 总是返回默认值。自定义合法主题名原样传入 `theme-<name>` 类；模板未定义对应 CSS 时使用默认外观，也兼容缺少 `theme` 的旧上下文。内置春节红金、中秋暖琥珀/靛蓝、国庆红和四季柔和配色，市场涨跌仍保持红涨绿跌；CSS 文本/背景对比度由单元测试校验 WCAG AA。主模板不展示每日英语，其 `moyuren:show-daily-english` 为 `false`；可爱模板为 `true`。`GET /api/v1/templates` 的每项新增 `show_daily_english` 布尔字段，反映发现的模板元数据。

两套模板显示调休感知的休息日与下班倒计时。`next_rest_day` 包含 `date`（今天之后下一个休息日的 ISO 日期）、`days_left`、`is_today_off`、`name`（下一个休息日的法定假日名或 null）及 `today_name`（今天的法定假日名或 null）；原有 `weekend.days_left` / `weekend.is_weekend` 保持兼容。holiday-cn 的休假区间优先，其余日期复用日历服务判断补班、法定假日和普通周末；缺少节假日数据时退回普通周末规则，最多搜索 31 天。假期列表将区间内的节日合并为如“劳动节 · 青年节”，合并后再取前 10 项。

`off_work` 在休息日为 null，工作日下班前为 `{hours, minutes, end_time}`，到达或超过下班时间为 `{done: true}`。倒计时使用同一次生成的业务时间快照，图片按配置的调度时间生成，不会实时跳动。渲染器按内容实际高度收缩或扩展截图，最高 4000 像素；主模板新闻自然排列，短趣味内容使用更大字号，两套模板页脚提高了文字对比度。

| 字段（`templates.config.text_limits.*`） | 默认值 | 内容 |
| --- | --- | --- |
| `news_item` | 120 | 每条新闻字符数 |
| `news_items` | 15 | 新闻条数 |
| `fun_content` | 300 | 趣味内容字符数 |
| `kfc_text` | 300 | KFC 文案字符数 |
| `daily_english_definition` | 200 | 每日英语释义字符数 |
| `daily_english_translation` | 200 | 每日英语翻译字符数 |
| `holiday_name` | 20 | 节假日及节日名称字符数 |

### 调度配置说明

- `scheduler.mode` 支持 `daily` 与 `hourly`
- 调度时间按 `timezone.business` 业务时区解释，不受宿主机时区影响
- 错过的触发允许延迟 300 秒执行，多次错过合并为一次执行，每个任务最多同时运行一个实例
- 当 `mode=hourly` 时，任务会在每小时的 `scheduler.minute_of_hour` 分触发
- 当 `mode=daily` 时，任务会按 `scheduler.daily_times` 中每个 `HH:MM` 时间触发
- `mode=hourly` 时 `daily_times` 会被忽略，建议保留以便快速回退到 `daily`
- 环境变量覆盖：`MOYUREN_SCHEDULER_MODE`、`MOYUREN_SCHEDULER_DAILY_TIMES`、`MOYUREN_SCHEDULER_MINUTE_OF_HOUR`

### 缓存目录结构

```
cache/
├── data/              # 日期数据文件
│   ├── 2026-02-09.json
│   └── 2026-02-10.json
├── images/            # 生成的图片文件
│   ├── moyuren_20260209_072232.jpg
│   ├── moyuren_20260210_183000.jpg
│   ├── moyuren_cute_20260209_072232.jpg
│   └── moyuren_cute_20260210_183000.jpg
├── daily/             # 日级缓存（数据源）
│   ├── news.json
│   ├── fun_content.json
│   ├── kfc.json
│   ├── daily_english.json
│   └── holidays.json
├── holidays/          # 节假日原始年度数据
│   ├── 2025.json
│   └── 2026.json
└── .generation.lock   # 生成锁文件
```

`data/YYYY-MM-DD.json` 由 `DailyDataFile` 统一读写并通过临时文件原子替换。必需字段为 `date`（有效的 `YYYY-MM-DD`）、`updated`（显示时区的时间字符串）、`updated_at`（非负整数毫秒时间戳，不接受布尔值）和 `images`（模板名到纯文件名的映射，禁止路径分隔符和 `..`）。`schema_version` 当前为 `1`，旧文件缺少该字段时按版本 1 读取；未知字段会保留。

可选字段包括 `image_variants`、`weekday`、`lunar_date`、`is_crazy_thursday`、`date_info`、`weekend`、`solar_term`、`guide`、`holidays`、`news_list`、`news_meta`、`fun_content`、`kfc_content`、`kfc_content_full`、`stock_indices`、`gold_price`、`daily_english`、`is_stale`、`source_date` 和 `stale_sources`。`stale_sources` 记录降级源的缓存命名空间，详情 API 仅公开新鲜度标志和新闻日期。`fun_content.type` 使用数据源端点名称，默认内容为 `default`；缺少类型的旧缓存仍兼容。`schema_version` 仅用于文件格式，不出现在公共 API 的简单或详情 JSON 响应中。

保留期内的图片按模板和日期清理，`cache.max_images_per_template_per_day` 默认为 3（最小为 1），按文件名时间保留最新图片。所有数据 JSON 引用的图片优先保留并计入数量，即使引用数超过上限也不会裁剪；数据 JSON 无法读取或格式无效时，跳过对应日期的图片裁剪。

可选字段 `image_variants` 为模板名到变体数组的映射，例如 `{"moyuren": [{"filename": "moyuren_20261029_060000.webp", "format": "webp", "width": 2382}]}`；列表包含原始 JPEG 和成功的派生图片，`width` 为正整数，文件名校验与 `images` 相同。旧文件缺少此字段或为 `null` 时仍可加载，`schema_version` 保持 1。

变体随其原始 JPEG 一同清理并计入释放字节数，每日数量上限仅统计原始 JPEG。数据 JSON 引用的原图及所有派生图片受到保护（包括 GET 补建但未记录的变体）；原图已不存在且无数据引用的孤立变体会删除。静态发布复制已存在的变体，并在发布 JSON 的 `image_variants` 中列出：日期目录的 `data.json` 引用 `moyuren.*`，根目录 `latest.json` 引用 `latest.*`；`render_once.py` 同样生成变体。

### 配置示例

每次生成固定一次业务时间，用于日历、星期四判断、JSON 日期和图片文件名；跨午夜完成时仍保留开始生成的业务日期。服务写入 JSON 时，`updated` 按显示时区记录实际写入时间，`updated_at` 使用该写入时刻的毫秒时间戳。静态发布的历史 Markdown/TXT 引用当天图片，`latest.*` 引用 `latest.jpg`。

```yaml
server:
  host: "0.0.0.0"
  port: 8000
  base_domain: "https://example.com"

timezone:
  business: "Asia/Shanghai"
  display: "local"

paths:
  cache_dir: "cache"

scheduler:
  mode: "daily"
  daily_times:
    - "06:00"
    - "18:00"
  minute_of_hour: 0

# 每小时模式示例（每小时第 0 分执行）
# scheduler:
#   mode: "hourly"
#   daily_times:
#     - "06:00"   # hourly 模式下会被忽略，仅用于回退 daily 时复用
#   minute_of_hour: 0

# 60s 公共实例（news / fun_content / crazy_thursday / gold_price 共享）
instances:
  list_url: "https://docs.60s-api.viki.moe/7306811m0.md"
  urls: []
  allowlist: null  # 不筛选自动发现实例；如 ["api.example.com", "*.example.org"]

data_sources:
  - type: "news"
    url: "https://60s.viki.moe/v2/60s"
    timeout_sec: 10
    params:
      "force-update": "false"
    # 主接口失败时按序回退：公共实例（全局 instances 配置）→ 60s 静态镜像

  - type: "fun_content"
    timeout_sec: 5
    endpoints:
      - name: "dad_joke"
        url: "https://60s.viki.moe/v2/dad-joke"
        data_path: "data.content"
        display_title: "🤣 冷笑话"
      - name: "hitokoto"
        url: "https://60s.viki.moe/v2/hitokoto"
        data_path: "data.hitokoto"
        display_title: "💬 一言"

  - type: "crazy_thursday"
    enabled: true
    url: "https://60s.viki.moe/v2/kfc"
    timeout_sec: 5

  - type: "holiday"
    enabled: true
    timeout_sec: 10

  - type: "stock_index"
    quote_url: "https://push2delay.eastmoney.com/api/qt/ulist.np/get"
    timeout_sec: 5
    secids:
      - {secid: "1.000001", name: "上证指数", market: "A"}
      - {secid: "0.399001", name: "深证成指", market: "A"}
      - {secid: "0.399006", name: "创业板指", market: "A"}
      - {secid: "100.HSI", name: "恒生指数", market: "HK"}
      - {secid: "100.DJIA", name: "道琼斯", market: "US"}
    market_timezones:
      A: "Asia/Shanghai"
      HK: "Asia/Hong_Kong"
      US: "America/New_York"
    cache_ttl_sec: 60

    # 兼容旧格式，例如：secids: ["1.000001", "100.HSI"]。
    # 已知指数使用内置中文名和市场；未知代码按前缀推断市场，无法推断时须使用对象格式。

  - type: "gold_price"
    url: "https://60s.viki.moe/v2/gold-price"
    timeout_sec: 5

templates:
  default: "moyuren"
  dir: "templates"
  config:
    device_scale_factor: 3
    jpeg_quality: 100
    use_china_cdn: true
    local_fonts_dir: "templates/fonts"
    remote_font_fallback: true
    work_end_time: "18:00"  # 业务时区下班时间（HH:MM）
    themes:
      enabled: true
      festival_themes: {除夕: spring_festival, 春节: spring_festival, 元宵节: spring_festival, 中秋节: mid_autumn, 国庆节: national_day}
      season_themes: true
    allowed_resource_hosts: ["fonts.googleapis.com", "fonts.googleapis.cn", "fonts.gstatic.com", "fonts.gstatic.cn"]
    remote_resource_max_redirects: 5
    text_limits:
      news_item: 120
      news_items: 15
      fun_content: 300
      kfc_text: 300
      daily_english_definition: 200
      daily_english_translation: 200
      holiday_name: 20

cache:
  retain_days: 30

ops:
  api_key: ""  # 运维 API Key（留空则禁用 ops 端点，所有请求返回 401）

logging:
  level: "INFO"
  file: "logs/app.log"
```

## 目录结构

```text
moyuren_server/
├── .github/workflows/   # CI/CD 工作流
├── app/
│   ├── main.py           # 应用入口
│   ├── api/v1/           # API 路由
│   ├── core/             # 配置、调度、错误处理
│   ├── services/         # 业务逻辑
│   │   ├── daily_cache.py # 日级缓存抽象基类
│   │   ├── fetcher.py    # 数据获取
│   │   ├── instances.py  # 60s 公共实例发现与智能切换
│   │   ├── holiday.py    # 节假日服务
│   │   ├── fun_content.py # 趣味内容服务
│   │   ├── kfc.py        # 疯狂星期四服务
│   │   ├── calendar.py   # 日历计算
│   │   ├── compute.py    # 数据计算
│   │   ├── stock_index.py # 大盘指数服务
│   │   ├── gold_price.py # 金价服务
│   │   ├── browser.py    # Playwright 浏览器管理
│   │   ├── renderer.py   # 图片渲染
│   │   ├── generator.py  # 图片生成流水线
│   │   └── cache.py      # 缓存清理
│   └── models/           # 数据模型
├── templates/            # Jinja2 模板与本地字体子集
│   └── fonts/            # WOFF2、fonts.css 与 OFL 许可证
├── scripts/              # 工具脚本
├── example/              # 示例文件
│   ├── moyuren_example.jpg        # 示例图片
│   ├── detail_normal.json         # 普通工作日响应示例
│   ├── detail_weekend.json        # 周末响应示例
│   ├── detail_crazy_thursday.json # 疯狂星期四响应示例
│   ├── detail_holiday.json        # 节假日响应示例
│   └── detail_solar_term.json     # 节气当天响应示例
├── cache/                # 缓存目录（数据/图片/日级缓存）
├── logs/                 # 日志目录
├── tests/                # 测试
├── config.yaml           # 配置文件
└── docker-compose.yaml   # Docker 编排
```

## 许可证

AGPL-3.0

### 字体许可与重建

渲染器优先使用 `templates/fonts/` 中的 WOFF2 子集，并通过保留的虚拟域名 `fonts.moyuren.invalid` 提供给 Playwright；Google Fonts 仅用于补齐子集中缺失的字形。离线部署可将 `templates.config.remote_font_fallback` 设为 `false`。

Noto Sans SC 子集覆盖 GB2312 全部汉字；文楷与站酷快乐体覆盖 GB2312 一级汉字，并补充模板、节气和节日字符。本地目录或 `fonts.css` 缺失时，每个渲染器实例只记录一次警告，并使用已启用的 Google Fonts 回退；显式关闭远程回退时仍只使用系统字体，不发起 Google Fonts 请求。

Noto Sans SC、JetBrains Mono、Poppins、Nunito 与 ZCOOL KuaiLe 来自 [Google Fonts](https://github.com/google/fonts)，LXGW WenKai 来自 [lxgw/LxgwWenKai v1.530](https://github.com/lxgw/LxgwWenKai/releases/tag/v1.530)。WenKai 的 Medium 字重在本地 CSS 中映射为 700，以覆盖模板的粗体标题。这些字体均依照 SIL Open Font License 1.1 使用；各字体许可证副本位于 `templates/fonts/licenses/`。

从已下载的上游 TTF 文件重建字体包：

```bash
python scripts/build_fonts.py --src /path/to/fontsrc --out templates/fonts
```

重建工具是开发依赖，需要安装 `requirements-dev.txt`；生成结果包含子集化 WOFF2、`fonts.css` 和许可证副本。
