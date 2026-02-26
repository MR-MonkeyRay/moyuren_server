# Moyuren API

摸鱼日历图片生成服务 | FastAPI + Playwright | Python 3.12

## 架构

外部数据源（节假日/趣味内容/KFC/金价/股票指数/交易日历）→ DataFetcher → CalendarService（农历/节气/时区）→ DataComputer → ImageRenderer（Playwright）→ JPEG → CacheDir → API 端点

## 核心模块

| 模块 | 文件 | 职责 |
|------|------|------|
| 入口 | `app/main.py` | 应用生命周期、定时任务注册 |
| API | `app/api/v1/moyuren.py` | REST 端点 |
| Ops API | `app/api/v1/ops.py` | 运维端点（手动生成、缓存清理） |
| 模板 API | `app/api/v1/templates.py` | 模板列表查询 |
| 服务容器 | `app/core/services.py` | AppServices 服务容器 |
| 日级缓存 | `app/services/daily_cache.py` | 日级缓存抽象基类（自动过期、降级） |
| 缓存清理 | `app/services/cache.py` | 缓存清理服务 |
| 获取 | `app/services/fetcher.py` | 异步并行 HTTP 请求 |
| 日历 | `app/services/calendar.py` | 农历、节气、时区管理（CalendarService） |
| 节假日 | `app/services/holiday.py` | 中国法定节假日数据获取与处理 |
| 趣味内容 | `app/services/fun_content.py` | 随机获取冷笑话/一言/段子 |
| KFC | `app/services/kfc.py` | 疯狂星期四文案（周四刷新） |
| 金价 | `app/services/gold_price.py` | 实时金价数据获取与缓存 |
| 股票指数 | `app/services/stock_index.py` | 大盘指数实时行情（StockIndexService） |
| 每日英语 | `app/services/daily_english.py` | 每日英语单词服务（ECDICT + 随机 API） |
| 计算 | `app/services/compute.py` | 原始数据 → 模板上下文 |
| 渲染 | `app/services/renderer.py` | Jinja2 + Playwright 截图 |
| 浏览器 | `app/services/browser.py` | Playwright 浏览器生命周期管理 |
| 模板发现 | `app/services/template_discovery.py` | 自动扫描模板目录，解析 meta 标签 |
| 图片生成 | `app/services/generator.py` | 图片生成流水线（文件锁防并发、缓存管理） |
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
| GET | `/api/v1/moyuren` | 获取最新图片元数据（缓存不存在时自动生成） |
| GET | `/api/v1/templates` | 获取可用模板列表 |
| GET | `/api/v1/ops/generate` | 手动触发图片生成（需鉴权） |
| GET | `/api/v1/ops/cache/clean` | 清理过期缓存（需鉴权） |
| GET | `/static/{filename}` | 静态图片 |

## 配置

主配置：`config.yaml`，支持环境变量覆盖（`SERVER_HOST`、`SERVER_PORT` 等）

关键配置项：
- `server.*`：服务器配置（host/port/base_domain）
- `timezone.*`：时区配置（business/display）
- `paths.*`：路径配置（cache_dir）
- `scheduler.daily_times`：每日生成时间（如 `["06:00", "18:00"]`）
- `cache.retain_days`：缓存保留天数（默认 30 天）
- `ops.api_key`：Ops API 鉴权密钥（留空则禁用鉴权）
- `templates.dir`：模板目录路径（默认 `templates`，自动扫描 `*.html`）
- `logging.*`：日志配置
- `network.ghproxy_urls`：GitHub 代理 URL 列表（加速节假日数据/ECDICT 下载）
- `data_sources`：数据源配置统一在此列表中管理（news/fun_content/crazy_thursday/holiday/stock_index/gold_price/daily_english）

## 开发指引

- **修改数据处理**：`fetcher.py` → `compute.py` → 模板
- **修改节假日逻辑**：`holiday.py` → `compute.py` → 模板
- **修改趣味内容**：`fun_content.py` → `compute.py` → 模板
- **修改 KFC 功能**：`kfc.py` → `compute.py` → 模板
- **修改金价功能**：`gold_price.py` → `compute.py` → 模板
- **修改大盘指数**：`stock_index.py` → `compute.py` → 模板
- **修改每日英语**：`daily_english.py` → `compute.py` → 模板
- **修改日历/农历**：`calendar.py` → `compute.py`
- **添加新模板**：在 `templates/` 目录创建 HTML 文件，在 `<head>` 中添加 `<meta name="moyuren:viewport-width" content="794">` 等 meta 标签，重启后自动发现
- **添加 API**：在 `app/api/v1/` 创建路由 → `main.py` 注册
- **测试渲染**：`python scripts/render_once.py`

## 编码规范

- 类型注解必须
- 使用 `app/core/errors.py` 中的异常类
- 通过 `config.py` 读取配置，禁止硬编码
- 使用注入的 logger，禁止 print
- 修改完成后更新 README.md
- 提交代码后根据内容更新系统版本号

## 数据服务

- **节假日服务**：数据源 [holiday-cn](https://github.com/NateScarlet/holiday-cn)，缓存目录 `cache/holidays/`
- **金价服务**：数据源外部金价 API，日级缓存自动过期
- **股票指数服务**：数据源东方财富 API，实时行情数据
- **每日英语服务**：数据源 [ECDICT](https://github.com/skywind3000/ECDICT) + 随机词 API，日级缓存自动过期

## 时间戳规范

API 返回格式 `YYYY/MM/DD HH:MM:SS`，模板显示通过 `format_datetime` 过滤器转为 `YYYY-MM-DD HH:MM`
