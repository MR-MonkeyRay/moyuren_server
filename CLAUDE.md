# Moyuren API

摸鱼日历图片生成服务 | FastAPI + Playwright | Python 3.12

## 架构

```text
[外部API] → DataFetcher ──────────┐
[holiday-cn] → HolidayService ────┼→ DataComputer → ImageRenderer → [JPEG]
[趣味API] → FunContentService ────┤                                    ↓
[KFC API] → KfcService (周四) ────┘                                    ↓
[客户端] ← GET /api/v1/moyuren ← CacheDir ← ScheduledTask/OnDemand ←───┘
```

## 核心模块

| 模块 | 文件 | 职责 |
| ---- | ---- | ---- |
| 入口 | `app/main.py` | 应用生命周期、定时任务注册 |
| API | `app/api/v1/moyuren.py` | REST 端点 |
| Ops API | `app/api/v1/ops.py` | 运维端点（手动生成、缓存清理） |
| 模板 API | `app/api/v1/templates.py` | 模板列表查询端点 |
| 日级缓存 | `app/services/daily_cache.py` | 日级缓存抽象基类（自动过期、降级策略） |
| 缓存清理 | `app/services/cache.py` | 缓存清理服务（数据+图片） |
| 获取 | `app/services/fetcher.py` | 异步并行 HTTP 请求 |
| 节假日 | `app/services/holiday.py` | 中国法定节假日数据获取与处理 |
| 趣味内容 | `app/services/fun_content.py` | 随机获取冷笑话/一言/段子等趣味内容 |
| KFC | `app/services/kfc.py` | 疯狂星期四文案获取（仅周四生效） |
| 计算 | `app/services/compute.py` | 原始数据 → 模板上下文 |
| 渲染 | `app/services/renderer.py` | Jinja2 + Playwright 截图 |
| 图片生成 | `app/services/generator.py` | 图片生成流水线（支持文件锁防并发、缓存管理） |
| 调度 | `app/core/scheduler.py` | APScheduler 定时任务 |
| 配置 | `app/core/config.py` | YAML + 环境变量 |

## 快速启动

```bash
# 本地开发
pip install -r requirements.txt && playwright install chromium
uvicorn app.main:app --reload

# Docker
docker-compose up -d
```

## API

| 方法 | 路径 | 说明 |
| ---- | ---- | ---- |
| GET | `/healthz` | 健康检查 |
| GET | `/readyz` | 就绪检查（含依赖验证） |
| GET | `/api/v1/moyuren` | 获取最新图片元数据 |
| GET | `/api/v1/templates` | 获取可用模板列表 |
| GET | `/api/v1/ops/generate` | 手动触发图片生成（需鉴权） |
| GET | `/api/v1/ops/cache/clean` | 清理过期缓存（需鉴权） |
| GET | `/static/{filename}` | 静态图片 |

> 注：当缓存图片不存在时，`/api/v1/moyuren` 会自动触发按需生成

## 配置

主配置：`config.yaml`，支持环境变量覆盖（`SERVER_HOST`、`SERVER_PORT` 等）

关键配置项：

- `scheduler.daily_times`: 每日生成时间，如 `["06:00", "18:00"]`
- `render.viewport_width/height`: 渲染视口尺寸
- `cache.retain_days`: 缓存保留天数（默认 30 天）
- `ops.api_key`: Ops API 鉴权密钥（留空则禁用鉴权）
- `fun_content.timeout_sec`: 趣味内容 API 超时时间
- `fun_content.endpoints`: 趣味内容 API 端点列表
- `crazy_thursday.enabled`: 是否启用疯狂星期四功能
- `crazy_thursday.url`: KFC 文案 API 地址
- `crazy_thursday.timeout_sec`: API 超时时间

## 开发指引

**修改数据处理**：`fetcher.py` → `compute.py` → `templates/moyuren.html`

**修改节假日逻辑**：`holiday.py` → `compute.py` → `templates/moyuren.html`

**修改趣味内容**：`fun_content.py` → `compute.py` → `templates/moyuren.html`

**修改 KFC 功能**：`kfc.py` → `compute.py` → `templates/moyuren.html`

**添加 API**：在 `app/api/v1/` 创建路由 → `main.py` 注册

**测试渲染**：`python scripts/render_once.py`

**优先使用Python环境**: `~/miniconda3/bin/python`

## 编码规范

- 类型注解必须
- 使用 `app/core/errors.py` 中的异常类
- 通过 `config.py` 读取配置，禁止硬编码
- 使用注入的 logger，禁止 print
- 修改完成后应当更新README.md
- 提交代码后修改应当根据内容更新系统版本号

## 节假日服务 (HolidayService)

**数据源**：[holiday-cn](https://github.com/NateScarlet/holiday-cn)

**数据结构**：
```python
{
    "name": "春节",
    "start_date": "2026-02-15",
    "end_date": "2026-02-23",
    "duration": 9,
    "days_left": 18,
    "color": None
}
```

**缓存目录**：`cache/holidays/`

## 时间戳规范

- **API 返回格式**：`YYYY/MM/DD HH:MM:SS`（如 `2026/02/01 07:22:32`）
- **模板显示**：`format_datetime` 过滤器转为 `YYYY-MM-DD HH:MM`
