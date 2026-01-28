"""Business computation service module."""

import logging
from datetime import datetime
from typing import Any

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
        now = CalendarService.now_shanghai()

        return {
            "date": self._compute_date(now),
            "weekend": self._compute_weekend(now),
            "solar_term": self._compute_solar_term(now),
            "guide": self._compute_guide(now),
            "history": self._compute_history(raw_data),
            "news_list": self._compute_news_list(raw_data),
            "news_meta": self._compute_news_meta(raw_data),
            "holidays": self._compute_holidays(raw_data),
        }

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
        """Extract or generate history content.

        Args:
            raw_data: Raw API data dictionary.

        Returns:
            History content dictionary.
        """
        history_data = raw_data.get("history")
        if history_data and "content" in history_data:
            return {"content": history_data["content"]}
        return {"content": self._DEFAULT_HISTORY}

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
                return {
                    "date": data.get("date"),
                    "api_updated": data.get("api_updated"),
                    "api_updated_at": data.get("api_updated_at"),
                }
        return {}

    def _compute_holidays(self, raw_data: dict[str, Any]) -> list[dict[str, Any]]:
        """Extract or generate holiday list.

        Args:
            raw_data: Raw API data dictionary, expects "holidays" key from HolidayService.

        Returns:
            List of holiday dictionaries with start_date, end_date, duration, days_left.
        """
        holidays_data = raw_data.get("holidays")
        if holidays_data and isinstance(holidays_data, list):
            return [
                {
                    "name": item.get("name", ""),
                    "start_date": item.get("start_date", ""),
                    "end_date": item.get("end_date", ""),
                    "duration": item.get("duration", 1),
                    "days_left": item.get("days_left", 0),
                    "color": item.get("color"),
                }
                for item in holidays_data
            ]
        # Default placeholder when no data available
        return []
