# Moyuren API

摸鱼日历图片生成服务 | FastAPI + Playwright | Python 3.12

## 架构

```text
[外部API] → DataFetcher → DataComputer → ImageRenderer → [JPEG]
                                                            ↓
[客户端] ← GET /api/v1/moyuren ← StateFile ← ScheduledTask ←┘
```

## 核心模块

| 模块 | 文件 | 职责 |
| ---- | ---- | ---- |
| 入口 | `app/main.py` | 应用生命周期、定时任务注册 |
| API | `app/api/v1/moyuren.py` | REST 端点 |
| 获取 | `app/services/fetcher.py` | 异步并行 HTTP 请求 |
| 计算 | `app/services/compute.py` | 原始数据 → 模板上下文 |
| 渲染 | `app/services/renderer.py` | Jinja2 + Playwright 截图 |
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
| GET | `/api/v1/moyuren` | 获取最新图片元数据 |
| GET | `/static/{filename}` | 静态图片 |

## 配置

主配置：`config.yaml`，支持环境变量覆盖（`SERVER_HOST`、`SERVER_PORT` 等）

关键配置项：

- `scheduler.daily_times`: 每日生成时间，如 `["06:00", "18:00"]`
- `render.viewport_width/height`: 渲染视口尺寸
- `cache.ttl_hours`: 图片保留时长

## 开发指引

**修改数据处理**：`fetcher.py` → `compute.py` → `templates/moyuren.html`

**添加 API**：在 `app/api/v1/` 创建路由 → `main.py` 注册

**测试渲染**：`python scripts/render_once.py`

**优先使用Python环境**: `~/miniconda3/bin/python`

## 编码规范

- 类型注解必须
- 使用 `app/core/errors.py` 中的异常类
- 通过 `config.py` 读取配置，禁止硬编码
- 使用注入的 logger，禁止 print
