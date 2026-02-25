"""Business computation service module."""

import logging
import math
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from app import __github_url__, __version__
from app.services.calendar import CalendarService, get_business_timezone, now_business

logger = logging.getLogger(__name__)


# 时区缩写映射表（映射到 UTC 偏移）
_TIMEZONE_ABBR_MAP: dict[str, timedelta] = {
    # 中国时区（本项目默认）
    "CST": timedelta(hours=8),  # China Standard Time
    "CCT": timedelta(hours=8),  # China Coast Time
    "BJT": timedelta(hours=8),  # Beijing Time
    # UTC 变体
    "UTC": timedelta(hours=0),
    "GMT": timedelta(hours=0),
    "Z": timedelta(hours=0),
    # 美国时区
    "EST": timedelta(hours=-5),  # Eastern Standard Time
    "EDT": timedelta(hours=-4),  # Eastern Daylight Time
    "CDT": timedelta(hours=-5),  # Central Daylight Time
    "MST": timedelta(hours=-7),  # Mountain Standard Time
    "MDT": timedelta(hours=-6),  # Mountain Daylight Time
    "PST": timedelta(hours=-8),  # Pacific Standard Time
    "PDT": timedelta(hours=-7),  # Pacific Daylight Time
    # 其他常见时区
    "JST": timedelta(hours=9),  # Japan Standard Time
    "KST": timedelta(hours=9),  # Korea Standard Time
    "IST": timedelta(hours=5, minutes=30),  # India Standard Time
    "AEST": timedelta(hours=10),  # Australian Eastern Standard Time
    "AEDT": timedelta(hours=11),  # Australian Eastern Daylight Time
}


def normalize_datetime(value: str, default_tz: timezone | None = None) -> str | None:
    """将各种格式的时间字符串规范化为 RFC3339 格式。

    支持的输入格式：
    - ISO 8601 / RFC3339: 2026-02-01T07:22:32+08:00
    - ISO 带 Z: 2026-02-01T07:22:32Z
    - 空格分隔: 2026-02-01 07:22:32
    - 带时区缩写: 2026-02-01 07:22:32 CST
    - 带 UTC/GMT 偏移: 2026-02-01 07:22 UTC+8, 2026-02-01 07:22 GMT+8
    - 带数字偏移: 2026-02-01 07:22 +0800, 2026-02-01 07:22 +08:00

    Args:
        value: 输入的时间字符串
        default_tz: 无时区信息时使用的默认时区，None 则使用业务时区

    Returns:
        RFC3339 格式字符串（如 2026-02-01T07:22:32+08:00），解析失败返回 None
    """
    if not value or not isinstance(value, str):
        return None

    value = value.strip()
    if not value:
        return None

    # 默认时区：使用业务时区的当前偏移
    if default_tz is None:
        biz_tz = get_business_timezone()
        offset = biz_tz.utcoffset(datetime.now(biz_tz))
        default_tz = timezone(offset if offset else timedelta(hours=8))

    # 尝试直接解析 ISO 格式（处理 Z 结尾）
    try:
        normalized = value.replace("Z", "+00:00") if value.endswith("Z") else value
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=default_tz)
        return dt.isoformat(timespec="seconds")
    except ValueError:
        pass

    # 提取并移除时区缩写或 UTC 偏移
    tz_offset: timedelta | None = None
    clean_value = value

    # 匹配 UTC+8, UTC+08, GMT+8, GMT+08 等格式
    utc_gmt_match = re.search(r"\b(?:UTC|GMT)([+-])(\d{1,2})(?::?(\d{2}))?\s*$", value, re.IGNORECASE)
    if utc_gmt_match:
        sign = 1 if utc_gmt_match.group(1) == "+" else -1
        hours = int(utc_gmt_match.group(2))
        minutes = int(utc_gmt_match.group(3)) if utc_gmt_match.group(3) else 0
        tz_offset = timedelta(hours=sign * hours, minutes=sign * minutes)
        clean_value = value[: utc_gmt_match.start()].strip()
    else:
        # 匹配尾部数字偏移：+0800, +08:00, -05:00, +8
        offset_match = re.search(r"\s([+-])(\d{1,2})(?::?(\d{2}))?\s*$", value)
        if offset_match:
            sign = 1 if offset_match.group(1) == "+" else -1
            hours = int(offset_match.group(2))
            minutes = int(offset_match.group(3)) if offset_match.group(3) else 0
            tz_offset = timedelta(hours=sign * hours, minutes=sign * minutes)
            clean_value = value[: offset_match.start()].strip()
        else:
            # 匹配时区缩写（如 CST, EST, GMT）- 不区分大小写
            abbr_match = re.search(r"\b([A-Za-z]{2,5})\s*$", value, re.IGNORECASE)
            if abbr_match:
                abbr = abbr_match.group(1).upper()
                if abbr in _TIMEZONE_ABBR_MAP:
                    tz_offset = _TIMEZONE_ABBR_MAP[abbr]
                    clean_value = value[: abbr_match.start()].strip()

    # 尝试解析清理后的时间字符串
    datetime_patterns = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M",
        "%Y/%m/%d %H:%M:%S",
        "%Y/%m/%d %H:%M",
        # 增加更多常见格式
        "%Y-%m-%d %H:%M:%S.%f",  # 带毫秒
        "%Y/%m/%d %H:%M:%S.%f",  # 带毫秒（斜杠分隔）
        "%Y%m%d %H:%M:%S",  # 无分隔符日期
        "%Y%m%d%H%M%S",  # 完全无分隔符
        "%d/%m/%Y %H:%M:%S",  # 日/月/年格式
        "%d-%m-%Y %H:%M:%S",  # 日-月-年格式
    ]

    for pattern in datetime_patterns:
        try:
            dt = datetime.strptime(clean_value, pattern)
            # 应用时区
            if tz_offset is not None:
                dt = dt.replace(tzinfo=timezone(tz_offset))
            else:
                dt = dt.replace(tzinfo=default_tz)
            return dt.isoformat(timespec="seconds")
        except ValueError:
            continue

    # 所有格式都无法解析
    logger.warning(f"Failed to normalize datetime: {value}")
    return None


class DomainDataAggregator:
    """Aggregate domain data from raw API responses."""

    # Week day mappings
    _WEEK_CN = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
    _WEEK_EN = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    # Default placeholder data
    _DEFAULT_GUIDE_YI = ["摸鱼", "喝茶", "休息", "学习"]
    _DEFAULT_GUIDE_JI = ["加班", "开会", "焦虑", "提需求"]
    _DEFAULT_HISTORY = "历史上的今天，世界依然在运转。在这个平凡的日子里，你也可以选择不把事情放在心上。"
    _DEFAULT_NEWS = [
        {"num": 1, "text": "今日天气晴朗，适合摸鱼。"},
        {"num": 2, "text": "研究表明，适当休息有助于提高工作效率。"},
        {"num": 3, "text": "距离周末不远了，保持心态平和。"},
        {"num": 4, "text": "记得多喝水，保持身体健康。"},
        {"num": 5, "text": "工作中遇到困难时，深呼吸，放轻松。"},
    ]

    def aggregate(self, raw_data: dict[str, Any]) -> dict[str, Any]:
        """Aggregate raw API data into domain data.

        Args:
            raw_data: Dictionary mapping endpoint names to their fetched data.
                      Values can be dict, list, or None depending on endpoint.

        Returns:
            A complete template context dictionary with all required variables.
        """
        # 使用业务时区的当前时间（用于节假日/节气/周末判断）
        now = now_business()

        # 计算各模块数据
        news_list = self._compute_news_list(raw_data)
        stock_indices = self._compute_stock_indices(raw_data)
        holidays = self._compute_holidays(now, raw_data)
        history = self._compute_history(raw_data)
        gold_price = self._compute_gold_price(raw_data)

        # 检测降级模式：当多个外部数据源都失败时
        is_fallback_mode = (
            news_list == self._DEFAULT_NEWS  # 新闻使用默认值
            and (stock_indices is None or stock_indices.get("is_data_missing"))  # 股票数据缺失
            and history.get("content") == self._DEFAULT_HISTORY  # 趣味内容使用默认值
        )

        return {
            "date": self._compute_date(now),
            "weekend": self._compute_weekend(now),
            **self._compute_progress(now),
            "solar_term": self._compute_solar_term(now),
            "guide": self._compute_guide(now),
            "history": history,
            "news_list": news_list,
            "news_meta": self._compute_news_meta(raw_data),
            "holidays": holidays,
            "kfc_content": self._compute_kfc(now, raw_data),
            "stock_indices": stock_indices,
            "gold_price": gold_price,
            # 降级模式标志
            "is_fallback_mode": is_fallback_mode,
            # 项目元信息
            "version": f"v{__version__}",
            "github_url": __github_url__,
        }

    def _compute_stock_indices(self, raw_data: dict[str, Any]) -> dict[str, Any] | None:
        """Compute stock indices data for template.

        Args:
            raw_data: Raw data dictionary.

        Returns:
            Dictionary with stock indices or None.
        """
        data = raw_data.get("stock_indices")
        # 增加类型校验
        if not isinstance(data, dict):
            # 数据完全缺失，返回带标志的空结构
            return {
                "indices": [],
                "updated": None,
                "is_stale": False,
                "is_data_missing": True,  # 标记数据获取失败
            }

        items_raw = data.get("items")
        if not isinstance(items_raw, list):
            return {
                "indices": [],
                "updated": None,
                "is_stale": False,
                "is_data_missing": True,
            }

        items = []
        for item in items_raw:
            # 过滤非字典元素
            if not isinstance(item, dict):
                continue

            # Format price with proper decimal places and type safety
            price = item.get("price")
            if price is not None:
                try:
                    price_str = f"{float(price):,.2f}"
                except (TypeError, ValueError):
                    price_str = "--"
            else:
                price_str = "--"

            # Format change percentage with type safety
            change_pct = item.get("change_pct")
            if change_pct is not None:
                try:
                    pct_value = float(change_pct)
                    change_pct_str = f"{'+' if pct_value > 0 else ''}{pct_value:.2f}%"
                except (TypeError, ValueError):
                    change_pct_str = "--"
            else:
                change_pct_str = "--"

            # 布尔类型字段规范化
            is_trading_day_raw = item.get("is_trading_day", True)
            if isinstance(is_trading_day_raw, str):
                is_trading_day = is_trading_day_raw.lower() not in ["false", "0", ""]
            elif isinstance(is_trading_day_raw, (int, float)):
                is_trading_day = bool(is_trading_day_raw)
            else:
                is_trading_day = bool(is_trading_day_raw)

            items.append(
                {
                    "name": item.get("name") or "",
                    "price": price_str,
                    "change_pct": change_pct_str,
                    "trend": item.get("trend") or "flat",
                    "market": item.get("market") or "",
                    "is_trading_day": is_trading_day,
                }
            )

        return {
            "indices": items,
            "updated": data.get("updated"),
            "is_stale": data.get("is_stale", False),
            "is_data_missing": False,  # 数据正常获取
        }

    def _compute_gold_price(self, raw_data: dict[str, Any]) -> dict[str, Any] | None:
        """Compute gold price data for template.

        Args:
            raw_data: Raw data dictionary.

        Returns:
            Dictionary with gold price info or None.
        """
        data = raw_data.get("gold_price")
        if not isinstance(data, dict):
            return None
        try:
            today_price = float(data["today_price"])
            sell_price = float(data["sell_price"])
            change = today_price - sell_price
            change_pct = (change / sell_price) * 100 if sell_price != 0 else 0.0
            trend = "up" if change > 0 else ("down" if change < 0 else "flat")
            return {
                "name": "今日金价",
                "price": f"{today_price:.2f}",
                "unit": data.get("unit", "元/克"),
                "spread_pct": f"{change_pct:+.2f}%",  # 买入价与卖出价的价差百分比
                "trend": trend,
            }
        except (KeyError, ValueError, TypeError) as e:
            logging.getLogger(__name__).debug(f"Gold price compute failed: {e}")
            return None

    def _compute_kfc(self, now: datetime, raw_data: dict[str, Any]) -> dict[str, Any] | None:
        """Compute KFC content if available and it's Thursday.

        Args:
            now: Current datetime.
            raw_data: Raw data dictionary.

        Returns:
            Dictionary with kfc content or None.
        """
        # 0=Mon, ... 3=Thu
        if now.weekday() == 3:
            content = raw_data.get("kfc_copy")
            if content:
                # 不截断文案，页面自适应内容高度
                return {"title": "CRAZY THURSDAY", "sub_title": "V我50", "content": content}
        return None

    def _compute_date(self, now: datetime) -> dict[str, Any]:
        """Compute date information including lunar calendar data.

        Args:
            now: Current datetime in Shanghai timezone.

        Returns:
            Date information dictionary with both gregorian and lunar data.
        """
        weekday = now.weekday()
        today = now.date()

        # Get lunar calendar info from CalendarService
        lunar_info = CalendarService.get_lunar_info(today)
        festivals = CalendarService.get_festivals(today)

        return {
            # Existing fields (backwards compatible)
            "year_month": now.strftime("%Y.%m"),
            "day": str(now.day),
            "week_cn": self._WEEK_CN[weekday],
            "week_en": self._WEEK_EN[weekday],
            "lunar_year": lunar_info["lunar_year"],
            "lunar_date": lunar_info["lunar_date"],
            # New fields
            "zodiac": lunar_info["zodiac"],
            "constellation": CalendarService.get_constellation(today),
            "moon_phase": CalendarService.get_moon_phase(today),
            "festival_solar": festivals["festival_solar"],
            "festival_lunar": festivals["festival_lunar"],
            "legal_holiday": festivals["legal_holiday"],
            "is_holiday": CalendarService.is_holiday(today),
        }

    def _compute_weekend(self, now: datetime) -> dict[str, int | bool]:
        """Compute days until weekend.

        Args:
            now: Current datetime.

        Returns:
            Dictionary with days_left until weekend and is_weekend flag.
        """
        weekday = now.weekday()
        # Saturday = 5, Sunday = 6
        is_weekend = weekday >= 5
        if is_weekend:
            days_left = 0
        else:
            days_left = 5 - weekday
        return {"days_left": days_left, "is_weekend": is_weekend}

    def _compute_progress(self, now: datetime) -> dict[str, float]:
        """Compute week/month/year progress percentages.

        Args:
            now: Current datetime in business timezone.

        Returns:
            Dictionary with week_progress, month_progress, year_progress in [0.0, 100.0].
        """
        now_ts = now.timestamp()

        week_start = (now - timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        week_end = week_start + timedelta(days=7)

        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if now.month == 12:
            month_end = month_start.replace(year=now.year + 1, month=1)
        else:
            month_end = month_start.replace(month=now.month + 1)

        year_start = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        year_end = year_start.replace(year=now.year + 1)

        def _calc_progress(start: datetime, end: datetime) -> float:
            start_ts = start.timestamp()
            end_ts = end.timestamp()
            total = end_ts - start_ts
            if total <= 0:
                return 0.0
            elapsed = now_ts - start_ts
            value = round(elapsed / total * 100, 2)
            return min(100.0, max(0.0, value))

        return {
            "week_progress": _calc_progress(week_start, week_end),
            "month_progress": _calc_progress(month_start, month_end),
            "year_progress": _calc_progress(year_start, year_end),
        }

    def _compute_solar_term(self, now: datetime) -> dict[str, Any]:
        """Compute next solar term using CalendarService.

        Args:
            now: Current datetime.

        Returns:
            Solar term information dictionary.
        """
        return CalendarService.get_solar_term_info(now.date())

    def _compute_guide(self, now: datetime) -> dict[str, list[str]]:
        """Compute yi/ji guide using CalendarService.

        Args:
            now: Current datetime.

        Returns:
            Dictionary with yi and ji lists.
        """
        yi_ji = CalendarService.get_yi_ji(now.date())

        # Use CalendarService data if available, otherwise fall back to defaults
        yi = yi_ji["yi"][:4] if yi_ji["yi"] else self._DEFAULT_GUIDE_YI[:2]
        ji = yi_ji["ji"][:4] if yi_ji["ji"] else self._DEFAULT_GUIDE_JI[:2]

        return {"yi": yi, "ji": ji}

    def _compute_history(self, raw_data: dict[str, Any]) -> dict[str, str]:
        """Extract fun content for display.

        Args:
            raw_data: Raw data dictionary containing 'fun_content' key.

        Returns:
            Dictionary with 'title' and 'content' keys.
        """
        fun_content = raw_data.get("fun_content")
        if fun_content and isinstance(fun_content, dict):
            return {
                "title": fun_content.get("title") or "🐟 摸鱼小贴士",
                "content": fun_content.get("content") or self._DEFAULT_HISTORY,
            }
        return {"title": "🐟 摸鱼小贴士", "content": self._DEFAULT_HISTORY}

    def _compute_news_list(self, raw_data: dict[str, Any]) -> list[dict[str, Any]]:
        """Extract or generate news list.

        Args:
            raw_data: Raw API data dictionary.

        Returns:
            List of news items.
        """
        news_data = raw_data.get("news")
        # Handle new API format: { code, data: { news: [...] } }
        if isinstance(news_data, dict):
            data = news_data.get("data")
            if isinstance(data, dict):
                news_items = data.get("news")
                if isinstance(news_items, list):
                    return [{"num": i + 1, "text": str(item)} for i, item in enumerate(news_items)]
        # Handle legacy format: [{ text: "..." }, ...]
        if news_data and isinstance(news_data, list):
            return [
                {
                    "num": i + 1,
                    "text": item.get("text", "") if isinstance(item, dict) else str(item),
                }
                for i, item in enumerate(news_data)
            ]
        return self._DEFAULT_NEWS

    def _compute_news_meta(self, raw_data: dict[str, Any]) -> dict[str, Any]:
        """Extract news API metadata.

        Args:
            raw_data: Raw API data dictionary.

        Returns:
            News metadata dictionary.
        """
        news_data = raw_data.get("news")
        if isinstance(news_data, dict):
            data = news_data.get("data")
            if isinstance(data, dict):
                # 兼容新旧字段名：优先使用 updated，回退到 api_updated
                updated_str = data.get("updated") or data.get("api_updated")
                # 规范化为 RFC3339 格式
                normalized_updated = normalize_datetime(updated_str) if updated_str else None
                return {
                    "date": data.get("date") or "",
                    "updated": normalized_updated,
                    "updated_at": data.get("updated_at") or data.get("api_updated_at") or "",
                }
        return {}

    def _compute_holidays(self, now: datetime, raw_data: dict[str, Any]) -> list[dict[str, Any]]:
        """整合三种数据源的节日数据。

        Args:
            now: Current datetime.
            raw_data: Raw API data dictionary, expects "holidays" key from HolidayService.

        Returns:
            List of holiday dictionaries with name, start_date, end_date, duration,
            days_left, is_legal_holiday, color.
        """
        today = now.date()

        # 获取三种数据源
        legal_holidays = raw_data.get("holidays", [])

        # CalendarService 返回值校验
        solar_festivals = CalendarService.get_upcoming_solar_festivals(today)
        if not isinstance(solar_festivals, list):
            solar_festivals = []

        lunar_festivals = CalendarService.get_upcoming_lunar_festivals(today)
        if not isinstance(lunar_festivals, list):
            lunar_festivals = []

        # 使用名称作为主键去重（法定假日优先）
        name_map: dict[str, dict[str, Any]] = {}

        # 特殊节假日名称简化映射
        # 用于处理地方性节假日等超长名称
        special_name_simplify = {
            "广西壮族自治区三月三": "三月三",
            "西藏百万农奴解放纪念日": "农奴解放日",
            "新疆肉孜节": "肉孜节",
            "新疆古尔邦节": "古尔邦节",
            "宁夏开斋节": "开斋节",
            "宁夏古尔邦节": "古尔邦节",
        }

        def simplify_holiday_name(name: str) -> str:
            """简化节假日名称，处理超长的地方性节假日名称."""
            # 先检查特殊映射
            if name in special_name_simplify:
                return special_name_simplify[name]
            # 通用规则：去除省份/自治区前缀
            prefixes = [
                "广西壮族自治区",
                "新疆维吾尔自治区",
                "西藏自治区",
                "内蒙古自治区",
                "宁夏回族自治区",
                "新疆",
                "西藏",
                "内蒙古",
                "宁夏",
                "广西",
            ]
            for prefix in prefixes:
                if name.startswith(prefix):
                    simplified = name[len(prefix) :]
                    if simplified:  # 确保简化后不为空
                        return simplified
            return name

        # 先加入法定假日（优先级最高）
        if legal_holidays and isinstance(legal_holidays, list):
            for h in legal_holidays:
                if not isinstance(h, dict):
                    continue
                raw_name = h.get("name", "")
                start_date = h.get("start_date", "")
                if raw_name and start_date:
                    # 简化节假日名称
                    name = simplify_holiday_name(raw_name)
                    # 确保 duration 和 days_left 为 int 类型
                    try:
                        duration = int(h.get("duration", 1))
                    except (TypeError, ValueError):
                        duration = 1
                    try:
                        days_left = int(h.get("days_left", 0))
                    except (TypeError, ValueError):
                        days_left = 0

                    # 布尔类型字段规范化
                    is_off_day_raw = h.get("is_off_day", True)
                    if isinstance(is_off_day_raw, str):
                        is_off_day = is_off_day_raw.lower() not in ["false", "0", ""]
                    else:
                        is_off_day = bool(is_off_day_raw) if is_off_day_raw is not None else True

                    name_map[name] = {
                        "name": name,
                        "start_date": start_date,
                        "end_date": h.get("end_date") or start_date,
                        "duration": duration,
                        "days_left": days_left,
                        "is_legal_holiday": True,
                        "color": "#E67E22",
                        "is_off_day": is_off_day,
                    }

        # 检查名称是否与已有法定假日重复（基于核心词匹配）
        # 白名单：这些名称不进行规范化处理
        preserved_names = {"春节", "元旦", "清明", "端午", "中秋", "国庆", "劳动"}

        def normalize_name(name: str) -> str:
            """提取节日名称的核心词，去除常见后缀."""
            # 白名单中的名称直接返回
            if name in preserved_names:
                return name
            # 按长度降序排列后缀，避免短后缀优先匹配
            suffixes = ["节假期", "假期", "节日", "节"]
            for suffix in suffixes:
                if name.endswith(suffix) and len(name) > len(suffix):
                    core = name[: -len(suffix)]
                    # 如果核心词在白名单中，返回核心词
                    if core in preserved_names:
                        return core
                    # 核心词太短（小于2字符）则不规范化
                    if len(core) < 2:
                        continue
                    return core
            return name

        def is_duplicate_name(name: str) -> bool:
            """检查是否与已有法定假日核心词重复."""
            core_name = normalize_name(name)
            for existing_name in name_map:
                existing_core = normalize_name(existing_name)
                # 核心词完全匹配才认为是重复
                if core_name == existing_core:
                    return True
            return False

        # 加入农历节日（如果该名称没有法定假日）
        for f in lunar_festivals:
            if not isinstance(f, dict):
                continue
            name = f.get("name")
            solar_date = f.get("solar_date")
            days_left = f.get("days_left")
            # 检查必需字段
            if not name or not solar_date or days_left is None:
                continue

            # 确保 days_left 为 int 类型
            try:
                days_left = int(days_left)
            except (TypeError, ValueError):
                continue

            if name not in name_map and not is_duplicate_name(name):
                name_map[name] = {
                    "name": name,
                    "start_date": solar_date,
                    "end_date": solar_date,
                    "duration": 1,
                    "days_left": days_left,
                    "is_legal_holiday": False,
                    "color": None,
                    "is_off_day": True,
                }

        # 加入公历节日（如果该名称没有）
        for f in solar_festivals:
            if not isinstance(f, dict):
                continue
            name = f.get("name")
            solar_date = f.get("solar_date")
            days_left = f.get("days_left")
            # 检查必需字段
            if not name or not solar_date or days_left is None:
                continue

            # 确保 days_left 为 int 类型
            try:
                days_left = int(days_left)
            except (TypeError, ValueError):
                continue

            if name not in name_map and not is_duplicate_name(name):
                name_map[name] = {
                    "name": name,
                    "start_date": solar_date,
                    "end_date": solar_date,
                    "duration": 1,
                    "days_left": days_left,
                    "is_legal_holiday": False,
                    "color": None,
                    "is_off_day": True,
                }

        # 按 days_left 排序并返回前10个
        result = sorted(name_map.values(), key=lambda x: x["days_left"])
        return result[:10]


class TemplateAdapter:
    """Template adapter for filling defaults and shaping output."""

    def adapt(self, domain_data: dict[str, Any]) -> dict[str, Any]:
        """Adapt domain data for template rendering.

        Args:
            domain_data: Domain data from DomainDataAggregator.

        Returns:
            Adapted data with defaults filled in.
        """
        data = dict(domain_data)

        if not data.get("history"):
            data["history"] = {"title": "🐟 摸鱼小贴士", "content": DomainDataAggregator._DEFAULT_HISTORY}
        if not data.get("news_list"):
            data["news_list"] = DomainDataAggregator._DEFAULT_NEWS
        if data.get("news_meta") is None:
            data["news_meta"] = {}
        if data.get("holidays") is None:
            data["holidays"] = []

        for key in ("week_progress", "month_progress", "year_progress"):
            value = data.get(key, 0.0)
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                data[key] = 0.0
                continue

            normalized = float(value)
            if not math.isfinite(normalized):
                data[key] = 0.0
                continue

            normalized = round(normalized, 2)
            if normalized < 0.0:
                normalized = 0.0
            elif normalized > 100.0:
                normalized = 100.0
            data[key] = normalized

        if "is_fallback_mode" not in data:
            news_list = data.get("news_list") or []
            history = data.get("history") or {}
            stock_indices = data.get("stock_indices")
            data["is_fallback_mode"] = (
                news_list == DomainDataAggregator._DEFAULT_NEWS
                and (stock_indices is None or stock_indices.get("is_data_missing"))
                and history.get("content") == DomainDataAggregator._DEFAULT_HISTORY
            )

        return data


class DataComputer:
    """Backward compatible wrapper for data computation."""

    def __init__(
        self,
        aggregator: DomainDataAggregator | None = None,
        adapter: TemplateAdapter | None = None,
    ) -> None:
        """Initialize DataComputer with optional aggregator and adapter.

        Args:
            aggregator: Domain data aggregator instance.
            adapter: Template adapter instance.
        """
        self._aggregator = aggregator or DomainDataAggregator()
        self._adapter = adapter or TemplateAdapter()

    def compute(self, raw_data: dict[str, Any]) -> dict[str, Any]:
        """Compute template context from raw data.

        Args:
            raw_data: Raw API data dictionary.

        Returns:
            Template context dictionary.
        """
        domain_data = self._aggregator.aggregate(raw_data)
        return self._adapter.adapt(domain_data)

    @property
    def aggregator(self) -> DomainDataAggregator:
        """Get the domain data aggregator."""
        return self._aggregator

    @property
    def adapter(self) -> TemplateAdapter:
        """Get the template adapter."""
        return self._adapter
