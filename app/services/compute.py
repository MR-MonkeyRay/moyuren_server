"""Business computation service module."""

import datetime
from typing import Any


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

    def compute(self, raw_data: dict[str, dict[str, Any] | None]) -> dict[str, Any]:
        """Transform raw API data into template context.

        Args:
            raw_data: Dictionary mapping endpoint names to their fetched data.

        Returns:
            A complete template context dictionary with all required variables.
        """
        now = datetime.datetime.now()

        return {
            "date": self._compute_date(now),
            "weekend": self._compute_weekend(now),
            "solar_term": self._compute_solar_term(),
            "guide": self._compute_guide(),
            "history": self._compute_history(raw_data),
            "news_list": self._compute_news_list(raw_data),
            "holidays": self._compute_holidays(raw_data),
        }

    def _compute_date(self, now: datetime.datetime) -> dict[str, str]:
        """Compute date information."""
        weekday = now.weekday()
        return {
            "year_month": now.strftime("%Y.%m"),
            "day": str(now.day),
            "week_cn": self._WEEK_CN[weekday],
            "week_en": self._WEEK_EN[weekday],
            "lunar_year": "乙巳年",
            "lunar_date": "腊月初九",
        }

    def _compute_weekend(self, now: datetime.datetime) -> dict[str, int]:
        """Compute days until weekend."""
        weekday = now.weekday()
        # Saturday = 5, Sunday = 6
        if weekday >= 5:
            days_left = 0
        else:
            days_left = 5 - weekday
        return {"days_left": days_left}

    def _compute_solar_term(self) -> dict[str, Any]:
        """Compute next solar term (placeholder)."""
        return {
            "name": "立春",
            "name_en": "Start of Spring",
            "days_left": 9,
        }

    def _compute_guide(self) -> dict[str, list[str]]:
        """Compute yi/ji guide (random selection from defaults)."""
        # For simplicity, use fixed values. Could use random.sample() for variety.
        return {
            "yi": self._DEFAULT_GUIDE_YI[:2],
            "ji": self._DEFAULT_GUIDE_JI[:2],
        }

    def _compute_history(self, raw_data: dict[str, dict[str, Any] | None]) -> dict[str, str]:
        """Extract or generate history content."""
        # Try to get from raw_data, otherwise use placeholder
        history_data = raw_data.get("history")
        if history_data and "content" in history_data:
            return {"content": history_data["content"]}
        return {"content": self._DEFAULT_HISTORY}

    def _compute_news_list(self, raw_data: dict[str, dict[str, Any] | None]) -> list[dict[str, Any]]:
        """Extract or generate news list."""
        news_data = raw_data.get("news")
        if news_data and isinstance(news_data, list):
            return [
                {"num": i + 1, "text": item.get("text", "")}
                for i, item in enumerate(news_data)
            ]
        return self._DEFAULT_NEWS

    def _compute_holidays(self, raw_data: dict[str, dict[str, Any] | None]) -> list[dict[str, Any]]:
        """Extract or generate holiday list."""
        holidays_data = raw_data.get("holidays")
        if holidays_data and isinstance(holidays_data, list):
            return [
                {
                    "name": item.get("name", ""),
                    "date": item.get("date", ""),
                    "days_left": item.get("days_left", 0),
                    "color": item.get("color"),
                }
                for item in holidays_data
            ]
        # Default placeholder holidays
        return [
            {
                "name": "春节假期",
                "date": "2026-02-17",
                "days_left": 22,
                "color": None,
            }
        ]
