# 项目指引

摸鱼日历图片生成服务，使用 Python 3.12、FastAPI、Playwright 和 Jinja2。
运行、部署、API 与配置详情见 [README.md](README.md)；配置模型见 `app/core/config.py`，仓库配置见 `config.yaml`。

## 代码入口

- `app/main.py`：生命周期、路由与静态目录；`app/core/bootstrap.py`：服务构建与启动任务；`app/core/services.py`：依赖容器。
- `app/api/v1/`：图片、RSS、模板与 Ops API；新增路由在 `app/main.py` 注册。
- `app/services/`：数据获取与缓存 → `compute.py` 组装模板上下文 → `generator.py` 协调生成 → `renderer.py` 渲染；农历、节气与时区复用 `calendar.py`。
- `templates/`：Jinja2 模板与本地字体；`scripts/`：渲染、静态发布与字体构建工具；`tests/`：按模块组织的测试。

## 修改约定

- 函数必须有类型注解；业务异常使用 `app/core/errors.py` 中的异常类。
- 配置通过 `app/core/config.py` 读取，不硬编码。环境变量统一使用 `MOYUREN_` 前缀；优先级为进程环境 → `.env` → YAML → 默认值。
- 使用注入的 logger，不用 `print`；日志使用 `%` 惰性格式化。
- 注释、docstring、OpenAPI 说明与命令行帮助使用中文；保留技术术语和 `Args:`、`Returns:`、`Raises:`、`Side Effects:` 标题。纯文档修改不改日志、异常、HTTP 响应、断言或样例数据。
- 字体构建会收集 HTML 注释和 `calendar.py` docstring 的字符；这些位置保留原文，避免纯翻译改变字体子集。
- 行为或配置变更同步调用方、测试和 README；不在代理指引中重复维护 API、配置字段或默认值清单。

## 关键约束

- 一次生成共享同一业务时间快照；保持业务/显示时区区分及过期数据标记。复用日级缓存与股票 TTL 缓存，不绕过生成锁或原子文件写入。
- Ops 仅使用 POST 和 Bearer 鉴权。模板预览不得联网、写正式图片/JSON、触发通知、计入正式生成指标或占用生成锁。
- 图片变体与通知失败不得影响主图片生成结果；历史图片和每日 JSON 的读取支持应保留。
- 主题选择复用 `CalendarService`；修改 `DataComputer` 配置时同步 bootstrap 与渲染脚本。未知或缺失主题保持默认外观，涨跌颜色保持红/绿；新增配色需补充 WCAG AA 文本/背景对比度测试。
- API 时间戳为 `YYYY/MM/DD HH:MM:SS`；模板通过 `format_datetime` 显示为 `YYYY-MM-DD HH:MM`。

## 验证

安装运行和开发依赖后，在仓库根目录执行：

```bash
ruff check app tests scripts
mypy app
coverage run -m pytest -m "not e2e" --tb=short
coverage report --fail-under=75
```

- 修改渲染或模板时，额外运行 `python scripts/render_once.py` 并检查图片；CI 烟测见 `.github/workflows/render-smoke.yml`。
- 模板元数据、离线预览与热发现规则见 [添加新模板](README.md#添加新模板)。
- 字体重建命令与许可证见 [字体许可与重建](README.md#字体许可与重建)；仅在确需更新字体时重建子集。
