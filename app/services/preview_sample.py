"""模板预览用的离线原始数据组装。"""

import logging
from datetime import datetime, timedelta
from typing import Any

from app.core.services import AppServices
from app.services.daily_cache import DailyCache


def sample_raw_data(now: datetime) -> dict[str, Any]:
    """构造确定性原始数据，足以渲染仓库内置的全部模板。"""
    business_date = now.date()
    date_text = business_date.isoformat()
    updated_text = now.replace(minute=0, second=0, microsecond=0).isoformat()
    holidays = [
        {
            "name": "周末休息日",
            "start_date": (business_date + timedelta(days=3)).isoformat(),
            "end_date": (business_date + timedelta(days=4)).isoformat(),
            "duration": 2,
            "days_left": 3,
            "is_off_day": True,
        },
        {
            "name": "摸鱼纪念日",
            "start_date": (business_date + timedelta(days=18)).isoformat(),
            "end_date": (business_date + timedelta(days=18)).isoformat(),
            "duration": 1,
            "days_left": 18,
            "is_off_day": True,
        },
    ]
    return {
        "news": {
            "code": 200,
            "data": {
                "date": date_text,
                "updated": updated_text,
                "updated_at": int(now.timestamp() * 1000),
                "news": [
                    "城市公园新增午间休息区",
                    "本周多地迎来晴朗天气",
                    "通勤线路推出便民服务",
                    "今日宜按时下班，享受生活",
                ],
            },
        },
        "holidays": holidays,
        "make_up_workdays": [],
        "fun_content": {
            "title": "🐟 摸鱼小贴士",
            "content": "每工作一段时间就起身活动一下，给眼睛和肩颈放个短假。",
            "type": "tip",
        },
        "kfc_copy": "今天也要好好吃饭。疯狂星期四，快乐不止五十块。",
        "stock_indices": {
            "items": [
                {
                    "name": "上证指数",
                    "price": 3288.42,
                    "change_pct": 0.68,
                    "trend": "up",
                    "market": "A",
                    "is_trading_day": True,
                },
                {
                    "name": "恒生指数",
                    "price": 20126.35,
                    "change_pct": -0.21,
                    "trend": "down",
                    "market": "HK",
                    "is_trading_day": True,
                },
            ],
            "updated": now.strftime("%Y/%m/%d %H:%M:%S"),
            "updated_at": int(now.timestamp() * 1000),
            "is_stale": False,
        },
        "gold_price": {
            "today_price": 786.50,
            "sell_price": 780.20,
            "unit": "元/克",
        },
        "daily_english": {
            "word": "balance",
            "phonetic": "/ˈbæləns/",
            "translation": "平衡；均衡",
            "definition": "a state in which different things have equal importance",
            "collins": 4,
            "oxford": True,
            "tag": ["cet4", "cet6"],
        },
        "stale_sources": [],
    }


def _load_cache(
    service: DailyCache[Any] | None,
    source_name: str,
    logger: logging.Logger,
) -> Any:
    """读取单个日级缓存，不调用可能联网的 ``get`` 方法。"""
    if service is None:
        return None
    try:
        return service.load_cache()
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        logger.warning("Failed to load preview cache for %s: %s", source_name, exc)
        return None


def cached_raw_data(
    services: AppServices,
    now: datetime,
    logger: logging.Logger,
) -> dict[str, Any]:
    """用日级缓存文件覆盖样例数据，组装预览数据；缺失来源保留样例。

    这里刻意只调用 ``load_cache``：绝不触发服务的 ``get``/抓取方法，
    不更新缓存文件，也不读取股票服务的进程内缓存。
    """
    result = sample_raw_data(now)

    cached_news = _load_cache(services.data_fetcher, "news", logger)
    if isinstance(cached_news, dict):
        for key, value in cached_news.items():
            if isinstance(key, str) and isinstance(value, dict):
                result[key] = value

    cached_holidays = _load_cache(services.holiday_service, "holidays", logger)
    if isinstance(cached_holidays, dict):
        holidays = cached_holidays.get("holidays")
        make_up_workdays = cached_holidays.get("make_up_workdays")
        if isinstance(holidays, list):
            result["holidays"] = holidays
        if isinstance(make_up_workdays, list):
            result["make_up_workdays"] = make_up_workdays

    cached_fun = _load_cache(services.fun_content_service, "fun_content", logger)
    if isinstance(cached_fun, dict):
        result["fun_content"] = cached_fun

    cached_kfc = _load_cache(services.kfc_service, "kfc", logger)
    if isinstance(cached_kfc, str):
        result["kfc_copy"] = cached_kfc

    cached_gold = _load_cache(services.gold_price_service, "gold_price", logger)
    if isinstance(cached_gold, dict):
        result["gold_price"] = cached_gold

    cached_english = _load_cache(
        services.daily_english_service, "daily_english", logger
    )
    if isinstance(cached_english, dict):
        result["daily_english"] = cached_english

    return result
