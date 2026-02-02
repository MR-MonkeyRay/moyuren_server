# Moyuren Server

摸鱼日历图片生成服务 | FastAPI + Playwright

## 预览

![摸鱼日历预览](https://api.monkeyray.net/api/v1/moyuren/latest)

## api体验

```bash
https://api.monkeyray.net/api/v1/moyuren
```

## 功能

- 每日定时生成摸鱼日历图片（支持多时间点）
- 按需生成：启动时或请求时若无可用图片则自动生成
- 60 秒读懂世界新闻
  - 数据源：[60s-api](https://60s.viki.moe)
- 农历信息与节气（干支年、生肖、二十四节气）
  - 数据源：[tyme4py](https://github.com/6tail/tyme4py)
- 节日倒计时整合（法定假日 + 农历/公历节日）
  - 数据源：[holiday-cn](https://github.com/NateScarlet/holiday-cn)
- 趣味内容随机展示（冷笑话、一言、段子、摸鱼语录）
  - 数据源：[60s-api](https://60s.viki.moe)
- 疯狂星期四：每周四自动展示 KFC 文案
  - 数据源：[60s-api](https://60s.viki.moe)
- 大盘指数实时行情（上证、深证、创业板、恒生、道琼斯）
  - 数据源：[东方财富](https://www.eastmoney.com)
  - 交易日历：[exchange_calendars](https://github.com/gerrymanoim/exchange_calendars)
- Playwright 高质量浏览器渲染
- 自动清理过期缓存
- RESTful API + 静态文件服务
- YAML 配置 + 环境变量覆盖

## 快速开始

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
mkdir -p static state logs
sudo chown -R 1000:1000 static state logs
```

## API

| 方法 | 路径 | 说明 |
| ---- | ---- | ---- |
| GET | `/healthz` | 健康检查 |
| GET | `/api/v1/moyuren` | 获取图片元信息（精简版） |
| GET | `/api/v1/moyuren/detail` | 获取图片内容详情 |
| GET | `/api/v1/moyuren/latest` | 直接获取最新图片文件（JPEG） |
| GET | `/static/{filename}` | 静态图片文件 |

> 注：当无可用图片时，API 会自动触发按需生成，请求会等待生成完成后返回结果（最长等待 60 秒）。

### 响应示例

#### GET /api/v1/moyuren - 图片元信息

```json
{
  "date": "2026-02-01",
  "updated": "2026-02-01T07:22:32+08:00",
  "updated_at": 1738372952000,
  "image": "https://api.monkeyray.net/static/moyuren_20260201_072232.jpg"
}
```

| 字段 | 类型 | 说明 |
| ---- | ---- | ---- |
| `date` | string | 图片日期 (YYYY-MM-DD) |
| `updated` | string | 生成时间 (RFC3339 格式，如 2026-02-01T07:22:32+08:00) |
| `updated_at` | number | 生成时间戳（13 位毫秒） |
| `image` | string | 图片完整 URL |

#### GET /api/v1/moyuren/latest - 直接获取图片

直接返回最新生成的 JPEG 图片文件，适用于：

- 在 HTML 中使用 `<img>` 标签直接嵌入
- 在 Markdown 中使用 `![](https://api.monkeyray.net/api/v1/moyuren/latest)` 显示
- 需要直接下载图片的场景

**响应**：`image/jpeg` 格式的二进制图片数据

#### GET /api/v1/moyuren/detail - 内容详情

```json
{
  "date": "2026-02-01",
  "updated": "2026-02-01T07:22:32+08:00",
  "updated_at": 1738372952000,
  "image": "https://api.monkeyray.net/static/moyuren_20260201_072232.jpg",
  "weekday": "星期日",
  "lunar_date": "正月初四",
  "fun_content": {
    "type": "dad_joke",
    "title": "🤣 冷笑话",
    "text": "程序员为什么喜欢黑暗模式？因为光会吸引 Bug。"
  },
  "countdowns": [
    { "name": "春节", "date": "2026-02-17", "days_left": 16 }
  ],
  "is_crazy_thursday": false,
  "kfc_content": null,
  "date_info": {
    "year_month": "2026.02",
    "day": "1",
    "week_cn": "星期日",
    "week_en": "Sun",
    "lunar_year": "乙巳年",
    "lunar_date": "正月初四",
    "zodiac": "蛇",
    "constellation": "水瓶座",
    "moon_phase": "峨眉月",
    "festival_solar": null,
    "festival_lunar": null,
    "legal_holiday": null,
    "is_holiday": false
  },
  "weekend": {
    "days_left": 5,
    "is_weekend": false
  },
  "solar_term": {
    "name": "立春",
    "name_en": "Beginning of Spring",
    "days_left": 3,
    "date": "2026-02-04",
    "is_today": false
  },
  "guide": {
    "yi": ["摸鱼", "喝茶", "休息", "学习"],
    "ji": ["加班", "开会", "焦虑", "提需求"]
  },
  "news_list": [
    "今日天气晴朗，适合摸鱼。",
    "研究表明，适当休息有助于提高工作效率。"
  ],
  "news_meta": {
    "date": "2026-02-01",
    "updated": "2026-02-01 06:00:00 CST",
    "updated_at": 1738368000000
  },
  "holidays": [
    {
      "name": "春节",
      "start_date": "2026-02-17",
      "end_date": "2026-02-23",
      "duration": 7,
      "days_left": 16,
      "is_legal_holiday": true,
      "is_off_day": true
    }
  ],
  "kfc_content_full": null,
  "stock_indices": {
    "items": [
      {
        "code": "000001",
        "name": "上证指数",
        "price": 3250.12,
        "change": 15.32,
        "change_pct": 0.47,
        "trend": "up",
        "market": "A",
        "is_trading_day": true
      }
    ],
    "updated": "2026-02-01 10:30",
    "updated_at": 1738384200000,
    "trading_day": { "A": true, "HK": true, "US": false },
    "is_stale": false
  }
}
```

| 字段 | 类型 | 说明 |
| ---- | ---- | ---- |
| `date` | string | 图片日期 (YYYY-MM-DD) |
| `updated` | string | 生成时间 (RFC3339 格式) |
| `updated_at` | number | 生成时间戳（13 位毫秒） |
| `image` | string | 图片完整 URL |
| `weekday` | string | 星期几（中文） |
| `lunar_date` | string | 农历日期 |
| `fun_content` | object | 趣味内容（type: dad_joke/hitokoto/duanzi/moyu_quote） |
| `countdowns` | array | 节假日倒计时列表（精简版） |
| `is_crazy_thursday` | boolean | 是否为周四 |
| `kfc_content` | string | KFC 文案内容（仅周四有值） |
| `date_info` | object | 完整日期信息（年月、农历、生肖、星座、月相、节日） |
| `weekend` | object | 周末倒计时（days_left, is_weekend） |
| `solar_term` | object | 节气信息（名称、天数、日期） |
| `guide` | object | 宜忌指南（yi, ji 列表） |
| `news_list` | array | 新闻文本列表 |
| `news_meta` | object | 新闻元数据（date, updated, updated_at） |
| `holidays` | array | 详细节假日列表（含法定假日标识、时长） |
| `kfc_content_full` | object | 完整 KFC 对象（title, sub_title, content） |
| `stock_indices` | object | 大盘指数数据（items: 指数列表, trading_day: 交易日状态） |

## 配置

配置文件：`config.yaml`

### 主要配置项

| 配置项 | 环境变量 | 说明 |
| ------ | -------- | ---- |
| `server.port` | `SERVER_PORT` | 服务端口 |
| `server.base_domain` | `SERVER_BASE_DOMAIN` | 图片 URL 前缀 |
| `scheduler.daily_times` | `SCHEDULER_DAILY_TIMES` | 生成时间（逗号分隔） |
| `render.viewport_width` | `RENDER_VIEWPORT_WIDTH` | 视口宽度 |
| `render.viewport_height` | `RENDER_VIEWPORT_HEIGHT` | 视口最小高度 |
| `render.device_scale_factor` | `RENDER_DEVICE_SCALE_FACTOR` | 缩放因子 |
| `cache.ttl_hours` | `CACHE_TTL_HOURS` | 缓存保留时长 |
| `logging.level` | `LOG_LEVEL` | 日志级别 |
| `holiday.mirror_urls` | `HOLIDAY_MIRROR_URLS` | GitHub 代理镜像站（逗号分隔） |
| `holiday.timeout_sec` | `HOLIDAY_TIMEOUT_SEC` | 节假日数据请求超时 |
| `fun_content.timeout_sec` | - | 趣味内容 API 超时 |
| `fun_content.endpoints` | - | 趣味内容 API 端点列表（仅 YAML） |
| `crazy_thursday.enabled` | - | 是否启用疯狂星期四功能 |
| `crazy_thursday.url` | - | KFC 文案 API 地址 |
| `crazy_thursday.timeout_sec` | - | KFC API 超时时间 |

### 配置示例

```yaml
server:
  port: 8000
  base_domain: "https://example.com"

scheduler:
  daily_times:
    - "06:00"
    - "18:00"

render:
  viewport_width: 794
  viewport_height: 1123
  device_scale_factor: 3
  jpeg_quality: 100

holiday:
  # GitHub 代理镜像站前缀列表
  # 程序自动拼接为：https://ghfast.top/raw.githubusercontent.com/NateScarlet/holiday-cn/master/{year}.json
  # 留空则直接使用 GitHub 原始源
  mirror_urls:
    - "https://ghfast.top/"
  timeout_sec: 10

fun_content:
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
```

## 目录结构

```text
moyuren_server/
├── app/
│   ├── main.py           # 应用入口
│   ├── api/v1/           # API 路由
│   ├── core/             # 配置、调度、错误处理
│   ├── services/         # 业务逻辑
│   │   ├── fetcher.py    # 数据获取
│   │   ├── holiday.py    # 节假日服务
│   │   ├── fun_content.py # 趣味内容服务
│   │   ├── kfc.py        # 疯狂星期四服务
│   │   ├── calendar.py   # 日历计算
│   │   ├── compute.py    # 数据计算
│   │   ├── renderer.py   # 图片渲染
│   │   ├── generator.py  # 图片生成流水线
│   │   └── cache.py      # 缓存清理
│   └── models/           # 数据模型
├── templates/            # Jinja2 模板
├── scripts/              # 工具脚本
├── config.yaml           # 配置文件
└── docker-compose.yaml   # Docker 编排
```

## 许可证

AGPL-3.0
