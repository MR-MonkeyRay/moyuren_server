# Moyuren API

摸鱼日历 API 服务 - 基于 FastAPI 和 Playwright 的日历图片生成服务。

## 功能特性

- **定时任务**：每日自动生成摸鱼日历图片
- **浏览器渲染**：使用 Playwright 进行高质量图片渲染
- **缓存管理**：自动清理过期缓存文件
- **RESTful API**：提供简洁的 API 接口
- **静态文件服务**：内置静态文件服务器
- **配置灵活**：支持 YAML 配置文件和环境变量

## 快速开始

### 本地运行

1. **克隆项目**
```bash
git clone <repository-url>
cd moyuren_server
```

2. **安装依赖**
```bash
# 安装 Python 依赖
pip install -r requirements.txt

# 安装 Playwright 浏览器
playwright install chromium
```

3. **配置**
```bash
# 复制配置文件模板
cp config.yaml config.yaml.local

# 根据需要修改配置
vim config.yaml
```

4. **启动服务**
```bash
uvicorn app.main:app --reload
```

服务将在 http://localhost:8000 启动。

### Docker 运行

1. **构建镜像**
```bash
docker build -t moyuren-api .
```

2. **使用 Docker Compose**
```bash
docker-compose up -d
```

3. **单独运行容器**
```bash
docker run -d \
  -p 8000:8000 \
  -v $(pwd)/static:/app/static \
  -v $(pwd)/state:/app/state \
  -v $(pwd)/logs:/app/logs \
  -e SERVER_PORT=8000 \
  -e LOG_LEVEL=INFO \
  moyuren-api
```

### 权限说明

容器以非 root 用户（`appuser`）运行，如果遇到挂载目录权限问题：

**方案一：在宿主机调整目录权限**
```bash
mkdir -p static state logs
sudo chown -R 1000:1000 static state logs
```

**方案二：使用 docker-compose.yaml 中的 user 配置**
```yaml
# 取消以下注释并设置宿主机用户的 UID/GID
user: "${USER_ID:-1000}:${GROUP_ID:-1000}"
```

然后运行：
```bash
USER_ID=$(id -u) GROUP_ID=$(id -g) docker-compose up -d
```

## 配置说明

### YAML 配置文件 (config.yaml)

```yaml
server:
  host: "0.0.0.0"           # 服务监听地址
  port: 8000                # 服务端口
  base_domain: "http://localhost:8000"  # 服务域名

paths:
  static_dir: "static"              # 图片输出目录
  template_path: "templates/moyuren.html"  # 模板文件路径
  state_path: "state/latest.json"   # 状态文件路径

scheduler:
  daily_time: "06:00"        # 每日生成时间

cache:
  ttl_hours: 24              # 缓存有效期（小时）

fetch:
  api_endpoints:
    - name: "news"
      url: "https://api.example.com/news"
      timeout_sec: 10

render:
  viewport_width: 794        # 视口宽度
  viewport_height: 1123      # 视口高度
  device_scale_factor: 2     # 设备缩放因子
  jpeg_quality: 90           # JPEG 质量

logging:
  level: "INFO"              # 日志级别
  file: "logs/app.log"       # 日志文件路径
```

### 环境变量

环境变量会覆盖 YAML 配置文件中的对应值：

| 环境变量 | 对应 YAML 路径 | 说明 |
|---------|---------------|------|
| `SERVER_HOST` | `server.host` | 服务监听地址 |
| `SERVER_PORT` | `server.port` | 服务端口 |
| `SERVER_BASE_DOMAIN` | `server.base_domain` | 服务域名 |
| `SCHEDULER_DAILY_TIME` | `scheduler.daily_time` | 每日生成时间 |
| `CACHE_TTL_HOURS` | `cache.ttl_hours` | 缓存有效期 |
| `RENDER_VIEWPORT_WIDTH` | `render.viewport_width` | 视口宽度 |
| `RENDER_VIEWPORT_HEIGHT` | `render.viewport_height` | 视口高度 |
| `RENDER_DEVICE_SCALE_FACTOR` | `render.device_scale_factor` | 设备缩放因子 |
| `RENDER_JPEG_QUALITY` | `render.jpeg_quality` | JPEG 质量 |
| `LOG_LEVEL` | `logging.level` | 日志级别 |
| `LOG_FILE` | `logging.file` | 日志文件路径 |
| `PATHS_STATIC_DIR` | `paths.static_dir` | 静态文件目录 |
| `PATHS_STATE_PATH` | `paths.state_path` | 状态文件路径 |

## API 文档

### GET /api/v1/moyuren

获取摸鱼日历图片信息。

**响应（JSON）：**

```json
{
  "date": "2024-01-15",
  "timestamp": "2024-01-15T06:00:00",
  "image": "http://localhost:8000/static/2024-01-15.jpg"
}
```

**状态码：**

- 200 OK：成功返回图片信息
- 404 Not Found：图片不存在
- 500 Internal Server Error：服务器内部错误

### GET /static/{filename}

访问静态文件。

**示例：**

```bash
curl http://localhost:8000/static/2024-01-15.jpg --output image.jpg
```

### GET /state/latest.json

获取最新图片状态信息。

**响应：**

```json
{
  "date": "2024-01-15",
  "timestamp": "2024-01-15T06:00:00",
  "filename": "2024-01-15.jpg"
}
```

## 开发

### 运行测试

```bash
pytest
```

## 许可证

AGPL License
