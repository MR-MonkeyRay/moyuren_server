"""Business computation service module."""

import logging
from datetime import datetime
from typing import Any

from app import __version__, __github_url__
from app.services.calendar import CalendarService

logger = logging.getLogger(__name__)


class DataComputer:
    """Data transformer that converts raw API data to template context."""

    # Week day mappings
    _WEEK_CN = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
    _WEEK_EN = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    # Default placeholder data
    _DEFAULT_GUIDE_YI = ["摸鱼", "喝茶", "休息", "学习"]
    _DEFAULT_GUIDE_JI = ["加班", "开会", "焦虑", "提需求"]
    _DEFAULT_HISTORY = (
        "历史上的今天，世界依然在运转。"
        "在这个平凡的日子里，你也可以选择不把事情放在心上。"
    )
    _DEFAULT_NEWS = [
        {"num": 1, "text": "今日天气晴朗，适合摸鱼。"},
        {"num": 2, "text": "研究表明，适当休息有助于提高工作效率。"},
        {"num": 3, "text": "距离周末不远了，保持心态平和。"},
        {"num": 4, "text": "记得多喝水，保持身体健康。"},
        {"num": 5, "text": "工作中遇到困难时，深呼吸，放轻松。"},
    ]

    def compute(self, raw_data: dict[str, Any]) -> dict[str, Any]:
        """Transform raw API data into template context.

        Args:
            raw_data: Dictionary mapping endpoint names to their fetched data.
                      Values can be dict, list, or None depending on endpoint.

        Returns:
            A complete template context dictionary with all required variables.
        """
        now = datetime.now()

        return {
            "date": self._compute_date(now),
            "weekend": self._compute_weekend(now),
            "solar_term": self._compute_solar_term(now),
            "guide": self._compute_guide(now),
            "history": self._compute_history(raw_data),
            "news_list": self._compute_news_list(raw_data),
            "news_meta": self._compute_news_meta(raw_data),
            "holidays": self._compute_holidays(now, raw_data),
            "kfc_content": self._compute_kfc(now, raw_data),
            # 项目元信息
            "version": f"v{__version__}",
            "github_url": __github_url__,
        }

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
                return {
                    "title": "CRAZY THURSDAY",
                    "sub_title": "V我50",
                    "content": content
                }
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

    def _compute_weekend(self, now: datetime) -> dict[str, int]:
        """Compute days until weekend.

        Args:
            now: Current datetime.

        Returns:
            Dictionary with days_left until weekend.
        """
        weekday = now.weekday()
        # Saturday = 5, Sunday = 6
        if weekday >= 5:
            days_left = 0
        else:
            days_left = 5 - weekday
        return {"days_left": days_left}

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
                "title": fun_content.get("title", "🐟 摸鱼小贴士"),
                "content": fun_content.get("content", self._DEFAULT_HISTORY)
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
                    return [
                        {"num": i + 1, "text": str(item)}
                        for i, item in enumerate(news_items)
                    ]
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
                return {
                    "date": data.get("date"),
                    "updated": data.get("updated") or data.get("api_updated"),
                    "updated_at": data.get("updated_at") or data.get("api_updated_at"),
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
        solar_festivals = CalendarService.get_upcoming_solar_festivals(today)
        lunar_festivals = CalendarService.get_upcoming_lunar_festivals(today)

        # 使用名称作为主键去重（法定假日优先）
        name_map: dict[str, dict[str, Any]] = {}

        # 先加入法定假日（优先级最高）
        if legal_holidays and isinstance(legal_holidays, list):
            for h in legal_holidays:
                if not isinstance(h, dict):
                    continue
                name = h.get("name", "")
                start_date = h.get("start_date", "")
                if name and start_date:
                    # 确保 duration 和 days_left 为 int 类型
                    try:
                        duration = int(h.get("duration", 1))
                    except (TypeError, ValueError):
                        duration = 1
                    try:
                        days_left = int(h.get("days_left", 0))
                    except (TypeError, ValueError):
                        days_left = 0
                    name_map[name] = {
                        "name": name,
                        "start_date": start_date,
                        "end_date": h.get("end_date", start_date),
                        "duration": duration,
                        "days_left": days_left,
                        "is_legal_holiday": True,
                        "color": "#E67E22",
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
                    core = name[:-len(suffix)]
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
            name = f["name"]
            if name not in name_map and not is_duplicate_name(name):
                name_map[name] = {
                    "name": name,
                    "start_date": f["solar_date"],
                    "end_date": f["solar_date"],
                    "duration": 1,
                    "days_left": f["days_left"],
                    "is_legal_holiday": False,
                    "color": None,
                }

        # 加入公历节日（如果该名称没有）
        for f in solar_festivals:
            name = f["name"]
            if name not in name_map and not is_duplicate_name(name):
                name_map[name] = {
                    "name": name,
                    "start_date": f["solar_date"],
                    "end_date": f["solar_date"],
                    "duration": 1,
                    "days_left": f["days_left"],
                    "is_legal_holiday": False,
                    "color": None,
                }

        # 按 days_left 排序并返回前10个
        result = sorted(name_map.values(), key=lambda x: x["days_left"])
        return result[:10]
