"""Calendar service module using tyme4py library."""

import logging
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from tyme4py.festival import LunarFestival, SolarFestival
from tyme4py.solar import SolarDay

logger = logging.getLogger(__name__)

TZ_SHANGHAI = ZoneInfo("Asia/Shanghai")

# 农历月份雅称映射
_LUNAR_MONTH_NAMES = {
    "正月": "正月",
    "一月": "正月",
    "二月": "二月",
    "三月": "三月",
    "四月": "四月",
    "五月": "五月",
    "六月": "六月",
    "七月": "七月",
    "八月": "八月",
    "九月": "九月",
    "十月": "十月",
    "十一月": "冬月",
    "十二月": "腊月",
}

# 节气英文名映射
_TERM_EN_MAP = {
    "立春": "Start of Spring",
    "雨水": "Rain Water",
    "惊蛰": "Awakening of Insects",
    "春分": "Spring Equinox",
    "清明": "Pure Brightness",
    "谷雨": "Grain Rain",
    "立夏": "Start of Summer",
    "小满": "Grain Buds",
    "芒种": "Grain in Ear",
    "夏至": "Summer Solstice",
    "小暑": "Minor Heat",
    "大暑": "Major Heat",
    "立秋": "Start of Autumn",
    "处暑": "End of Heat",
    "白露": "White Dew",
    "秋分": "Autumn Equinox",
    "寒露": "Cold Dew",
    "霜降": "Frost's Descent",
    "立冬": "Start of Winter",
    "小雪": "Minor Snow",
    "大雪": "Major Snow",
    "冬至": "Winter Solstice",
    "小寒": "Minor Cold",
    "大寒": "Major Cold",
}


class CalendarService:
    """Wrapper for tyme4py library to provide calendar data."""

    @staticmethod
    def get_lunar_info(dt: date) -> dict[str, str]:
        """Get lunar calendar information.

        Args:
            dt: The date to get lunar info for.

        Returns:
            Dict with keys: lunar_year (干支年), lunar_date (农历月日),
            zodiac (生肖), sixty_cycle (干支)
        """
        try:
            solar = SolarDay.from_ymd(dt.year, dt.month, dt.day)
            lunar = solar.get_lunar_day()
            lunar_month = lunar.get_lunar_month()
            lunar_year = lunar_month.get_lunar_year()
            sixty_cycle = lunar_year.get_sixty_cycle()

            # 获取农历月份名称并转换为雅称
            month_name = lunar_month.get_name()
            month_display = _LUNAR_MONTH_NAMES.get(month_name, month_name)

            return {
                "lunar_year": f"{sixty_cycle}年",
                "lunar_date": f"{month_display}{lunar.get_name()}",
                "zodiac": str(sixty_cycle.get_earth_branch().get_zodiac()),
                "sixty_cycle": str(sixty_cycle),
            }
        except Exception as e:
            logger.warning("Failed to get lunar info for %s: %s", dt, e)
            return {
                "lunar_year": "未知",
                "lunar_date": "未知",
                "zodiac": "未知",
                "sixty_cycle": "未知",
            }

    @staticmethod
    def get_solar_term_info(dt: date) -> dict[str, Any]:
        """Get next solar term information.

        Args:
            dt: The date to calculate from.

        Returns:
            Dict with keys: name, name_en, days_left, date
        """
        try:
            solar = SolarDay.from_ymd(dt.year, dt.month, dt.day)
            term = solar.get_term()

            # 获取下一个节气
            next_term = term.next(1)
            next_term_jd = next_term.get_julian_day()
            next_solar = next_term_jd.get_solar_day()
            next_date = date(
                next_solar.get_year(),
                next_solar.get_month(),
                next_solar.get_day(),
            )
            days_left = (next_date - dt).days

            term_name = next_term.get_name()
            return {
                "name": term_name,
                "name_en": _TERM_EN_MAP.get(term_name, term_name),
                "days_left": max(0, days_left),
                "date": next_date.isoformat(),
            }
        except Exception as e:
            logger.warning("Failed to get solar term info for %s: %s", dt, e)
            return {
                "name": "未知",
                "name_en": "Unknown",
                "days_left": 0,
                "date": "",
            }

    @staticmethod
    def get_constellation(dt: date) -> str:
        """Get constellation for the given date.

        Args:
            dt: The date to get constellation for.

        Returns:
            Constellation name with "座" suffix.
        """
        try:
            solar = SolarDay.from_ymd(dt.year, dt.month, dt.day)
            constellation = solar.get_constellation()
            name = str(constellation)
            # 添加"座"后缀（如果不存在）
            if not name.endswith("座"):
                name = f"{name}座"
            return name
        except Exception as e:
            logger.warning("Failed to get constellation for %s: %s", dt, e)
            return "未知"

    @staticmethod
    def get_moon_phase(dt: date) -> str:
        """Get moon phase for the given date.

        Args:
            dt: The date to get moon phase for.

        Returns:
            Moon phase name.
        """
        try:
            solar = SolarDay.from_ymd(dt.year, dt.month, dt.day)
            return str(solar.get_phase())
        except Exception as e:
            logger.warning("Failed to get moon phase for %s: %s", dt, e)
            return "未知"

    @staticmethod
    def get_festivals(dt: date) -> dict[str, str | None]:
        """Get festivals for the given date.

        Args:
            dt: The date to get festivals for.

        Returns:
            Dict with keys: festival_solar, festival_lunar, legal_holiday
        """
        try:
            solar = SolarDay.from_ymd(dt.year, dt.month, dt.day)
            lunar = solar.get_lunar_day()

            solar_festival = solar.get_festival()
            lunar_festival = lunar.get_festival()
            legal_holiday = solar.get_legal_holiday()

            return {
                "festival_solar": str(solar_festival) if solar_festival else None,
                "festival_lunar": str(lunar_festival) if lunar_festival else None,
                "legal_holiday": str(legal_holiday) if legal_holiday else None,
            }
        except Exception as e:
            logger.warning("Failed to get festivals for %s: %s", dt, e)
            return {
                "festival_solar": None,
                "festival_lunar": None,
                "legal_holiday": None,
            }

    @staticmethod
    def get_yi_ji(dt: date) -> dict[str, list[str]]:
        """Get yi (宜) and ji (忌) for the given date.

        Args:
            dt: The date to get yi/ji for.

        Returns:
            Dict with keys: yi, ji (each is a list of strings)
        """
        try:
            solar = SolarDay.from_ymd(dt.year, dt.month, dt.day)
            lunar = solar.get_lunar_day()

            recommends = lunar.get_recommends()
            avoids = lunar.get_avoids()

            return {
                "yi": [str(r) for r in recommends] if recommends else [],
                "ji": [str(a) for a in avoids] if avoids else [],
            }
        except Exception as e:
            logger.warning("Failed to get yi/ji for %s: %s", dt, e)
            return {
                "yi": [],
                "ji": [],
            }

    @staticmethod
    def is_holiday(dt: date) -> bool:
        """Check if the given date is a holiday.

        Args:
            dt: The date to check.

        Returns:
            True if it's a legal holiday, False otherwise.
        """
        try:
            solar = SolarDay.from_ymd(dt.year, dt.month, dt.day)
            legal_holiday = solar.get_legal_holiday()
            return legal_holiday is not None
        except Exception as e:
            logger.warning("Failed to check holiday for %s: %s", dt, e)
            return False

    @staticmethod
    def now_shanghai() -> datetime:
        """Get current datetime in Asia/Shanghai timezone.

        Returns:
            Current datetime with Asia/Shanghai timezone.
        """
        return datetime.now(TZ_SHANGHAI)

    @staticmethod
    def get_upcoming_solar_festivals(dt: date, count: int = 10) -> list[dict[str, Any]]:
        """获取从指定日期起未来的公历现代节日。

        Args:
            dt: 基准日期
            count: 返回数量

        Returns:
            List of dicts with keys: name, solar_date, days_left
        """
        result = []
        try:
            for year in (dt.year, dt.year + 1):
                for idx in range(20):  # 每年最多20个节日
                    try:
                        festival = SolarFestival.from_index(year, idx)
                        day = festival.get_day()
                        festival_date = date(day.get_year(), day.get_month(), day.get_day())
                        if festival_date >= dt:
                            days_left = (festival_date - dt).days
                            result.append({
                                "name": festival.get_name(),
                                "solar_date": festival_date.isoformat(),
                                "days_left": days_left,
                            })
                    except (IndexError, ValueError):
                        break  # 索引超出范围
                    except Exception as e:
                        logger.debug("Error processing solar festival %d-%d: %s", year, idx, e)
                        continue  # 其他异常继续处理下一个
            # 去重并按日期排序
            seen: set[str] = set()
            unique_result = []
            for item in sorted(result, key=lambda x: x["days_left"]):
                if item["name"] not in seen:
                    seen.add(item["name"])
                    unique_result.append(item)
            return unique_result[:count]
        except Exception as e:
            logger.warning("Failed to get solar festivals: %s", e)
            return []

    @staticmethod
    def get_upcoming_lunar_festivals(dt: date, count: int = 10) -> list[dict[str, Any]]:
        """获取从指定日期起未来的农历传统节日。

        Args:
            dt: 基准日期（公历）
            count: 返回数量

        Returns:
            List of dicts with keys: name, solar_date, days_left
        """
        result = []
        try:
            for year in (dt.year, dt.year + 1):
                for idx in range(20):  # 每年最多20个节日
                    try:
                        festival = LunarFestival.from_index(year, idx)
                        lunar_day = festival.get_day()
                        solar_day = lunar_day.get_solar_day()
                        festival_date = date(
                            solar_day.get_year(),
                            solar_day.get_month(),
                            solar_day.get_day()
                        )
                        if festival_date >= dt:
                            days_left = (festival_date - dt).days
                            result.append({
                                "name": festival.get_name(),
                                "solar_date": festival_date.isoformat(),
                                "days_left": days_left,
                            })
                    except (IndexError, ValueError):
                        break  # 索引超出范围
                    except Exception as e:
                        logger.debug("Error processing lunar festival %d-%d: %s", year, idx, e)
                        continue  # 其他异常继续处理下一个
            # 去重并按日期排序
            seen: set[str] = set()
            unique_result = []
            for item in sorted(result, key=lambda x: x["days_left"]):
                if item["name"] not in seen:
                    seen.add(item["name"])
                    unique_result.append(item)
            return unique_result[:count]
        except Exception as e:
            logger.warning("Failed to get lunar festivals: %s", e)
            return []
