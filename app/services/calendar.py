"""Calendar service module using tyme4py library."""

import logging
import os
from datetime import date, datetime, timedelta, timezone, tzinfo
from typing import Any
from zoneinfo import ZoneInfo

from tyme4py.festival import LunarFestival, SolarFestival
from tyme4py.solar import SolarDay

logger = logging.getLogger(__name__)


def get_local_timezone() -> tzinfo:
    """从环境获取时区。

    优先级：
    1. TZ 环境变量
    2. 系统本地时区（转换为等效 ZoneInfo 或固定偏移时区）
    3. 回退到 UTC

    Returns:
        时区对象（ZoneInfo 或 datetime.timezone）
    """
    # 1. 尝试使用 TZ 环境变量
    tz_name = os.environ.get("TZ")
    if tz_name:
        try:
            return ZoneInfo(tz_name)
        except (KeyError, ValueError, OSError) as e:
            logger.warning("Invalid TZ environment variable '%s': %s, falling back to local timezone", tz_name, e)

    # 2. 尝试获取系统本地时区
    try:
        local_dt = datetime.now().astimezone()
        local_tz = local_dt.tzinfo
        if local_tz is not None:
            tz_name = str(local_tz)
            # 尝试创建 ZoneInfo（如果是标准时区名称如 "Asia/Shanghai"）
            try:
                return ZoneInfo(tz_name)
            except (KeyError, ValueError, OSError):
                # 非标准时区名称，使用 UTC 偏移量构造等效时区
                offset = local_dt.utcoffset()
                if offset is not None:
                    total_seconds = int(offset.total_seconds())
                    sign = "+" if total_seconds >= 0 else "-"
                    abs_seconds = abs(total_seconds)
                    hours = abs_seconds // 3600
                    minutes = (abs_seconds % 3600) // 60
                    # 如果有分钟偏移，直接返回基于 UTC 偏移的固定时区对象
                    if minutes != 0:
                        logger.warning(
                            "Local timezone '%s' (offset %s%02d:%02d) cannot be converted to ZoneInfo, using fixed offset timezone",
                            tz_name,
                            sign,
                            hours,
                            minutes,
                        )
                        return timezone(timedelta(seconds=total_seconds))
                    # 构造 Etc/GMT 格式时区（注意符号相反）
                    if -12 <= (total_seconds // 3600) <= 14:
                        # Etc/GMT 时区符号与偏移相反
                        gmt_offset = -total_seconds // 3600
                        gmt_sign = "+" if gmt_offset > 0 else ("-" if gmt_offset < 0 else "")
                        try:
                            return ZoneInfo(f"Etc/GMT{gmt_sign}{abs(gmt_offset)}" if gmt_offset != 0 else "Etc/GMT")
                        except (KeyError, ValueError, OSError):
                            pass
                    logger.warning(
                        "Local timezone '%s' (offset %s%02d:%02d) cannot be converted to ZoneInfo, using UTC",
                        tz_name,
                        sign,
                        hours,
                        minutes,
                    )
    except OSError as e:
        logger.warning("Failed to get local timezone: %s, falling back to UTC", e)

    # 3. 回退到 UTC
    return ZoneInfo("UTC")


def get_timezone_label(dt: datetime | None = None) -> str:
    """获取时区标签用于显示。

    Args:
        dt: 可选的 datetime 对象，用于获取该时刻的时区偏移。
            如果为 None，则使用当前时间。

    Returns:
        时区标签字符串，如 "UTC+08" 或 "UTC-05"
    """
    if dt is None:
        tz = get_display_timezone()
        dt = datetime.now(tz)

    offset = dt.utcoffset()
    if offset is None:
        return "UTC"

    total_seconds = int(offset.total_seconds())
    # 使用绝对值计算，避免负偏移整除错误
    sign = "+" if total_seconds >= 0 else "-"
    abs_seconds = abs(total_seconds)
    hours = abs_seconds // 3600
    minutes = (abs_seconds % 3600) // 60

    if minutes == 0:
        return f"UTC{sign}{hours:02d}"
    else:
        return f"UTC{sign}{hours:02d}:{minutes:02d}"


# ============ 业务时区与显示时区 ============
# 模块级缓存变量
_business_timezone: tzinfo | None = None
_display_timezone: tzinfo | None = None


def _parse_timezone(tz_str: str) -> tzinfo:
    """解析时区字符串为时区对象。

    支持格式：
    - IANA 时区名称：Asia/Shanghai, America/New_York
    - UTC 偏移格式：UTC+8, UTC-5, UTC+05:30

    Returns:
        时区对象（ZoneInfo 或 datetime.timezone）
    """
    import re

    # 尝试直接作为 IANA 时区名称
    try:
        return ZoneInfo(tz_str)
    except (KeyError, ValueError):
        pass

    # 尝试解析 UTC±X 格式
    match = re.match(r"^UTC([+-])(\d{1,2})(?::(\d{2}))?$", tz_str, re.IGNORECASE)
    if match:
        sign = 1 if match.group(1) == "+" else -1
        hours = int(match.group(2))
        minutes = int(match.group(3) or 0)

        # 如果有分钟偏移，直接返回基于 UTC 偏移的固定时区对象
        if minutes != 0:
            return timezone(timedelta(hours=sign * hours, minutes=sign * minutes))

        # 转换为 Etc/GMT 格式（符号相反）
        if -12 <= sign * hours <= 14:
            gmt_offset = -sign * hours
            if gmt_offset == 0:
                return ZoneInfo("Etc/GMT")
            gmt_sign = "+" if gmt_offset > 0 else ""
            try:
                return ZoneInfo(f"Etc/GMT{gmt_sign}{gmt_offset}")
            except (KeyError, ValueError, OSError):
                pass

    logger.warning("Cannot parse timezone '%s', falling back to UTC", tz_str)
    return ZoneInfo("UTC")


def init_timezones(business_tz: str, display_tz: str) -> None:
    """初始化时区配置（应用启动时调用）。

    Args:
        business_tz: 业务时区名称（用于节假日/节气判断）
        display_tz: 显示时区名称，"local" 表示使用本地时区
    """
    global _business_timezone, _display_timezone

    _business_timezone = _parse_timezone(business_tz)

    if display_tz.lower() == "local":
        _display_timezone = get_local_timezone()
    else:
        _display_timezone = _parse_timezone(display_tz)

    logger.info("Timezones initialized: business=%s, display=%s", _business_timezone, _display_timezone)


def get_business_timezone() -> tzinfo:
    """获取业务时区。

    用于节假日/节气/周末等业务逻辑判断。
    如果未初始化，回退到 Asia/Shanghai（与节假日数据源保持一致）。
    """
    if _business_timezone is None:
        return ZoneInfo("Asia/Shanghai")
    return _business_timezone


def get_display_timezone() -> tzinfo:
    """获取显示时区。

    用于图片时间戳、API 响应时间等显示场景。
    如果未初始化，回退到本地时区。
    """
    if _display_timezone is None:
        return get_local_timezone()
    return _display_timezone


def now_business() -> datetime:
    """获取业务时区的当前时间。"""
    return datetime.now(get_business_timezone())


def today_business() -> date:
    """获取业务时区的今天日期。"""
    return now_business().date()


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
        """Get solar term information for the given date.

        If today is a solar term day, returns the current solar term with is_today=True.
        Otherwise, returns the next upcoming solar term with days_left countdown.

        Args:
            dt: The date to calculate from.

        Returns:
            Dict with keys: name, name_en, days_left, date, is_today
        """
        try:
            solar = SolarDay.from_ymd(dt.year, dt.month, dt.day)
            term = solar.get_term()

            # 获取当前节气的日期
            term_jd = term.get_julian_day()
            term_solar = term_jd.get_solar_day()
            term_date = date(
                term_solar.get_year(),
                term_solar.get_month(),
                term_solar.get_day(),
            )

            # 检查今天是否是节气当天
            if term_date == dt:
                term_name = term.get_name()
                return {
                    "name": term_name,
                    "name_en": _TERM_EN_MAP.get(term_name, term_name),
                    "days_left": 0,
                    "date": term_date.isoformat(),
                    "is_today": True,
                }

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
                "is_today": False,
            }
        except Exception as e:
            logger.warning("Failed to get solar term info for %s: %s", dt, e)
            return {
                "name": "未知",
                "name_en": "Unknown",
                "days_left": 0,
                "date": "",
                "is_today": False,
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
    def now_local() -> datetime:
        """Get current datetime in display timezone.

        Returns:
            Current datetime with display timezone.
        """
        return datetime.now(get_display_timezone())

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
                            result.append(
                                {
                                    "name": festival.get_name(),
                                    "solar_date": festival_date.isoformat(),
                                    "days_left": days_left,
                                }
                            )
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
                        festival_date = date(solar_day.get_year(), solar_day.get_month(), solar_day.get_day())
                        if festival_date >= dt:
                            days_left = (festival_date - dt).days
                            result.append(
                                {
                                    "name": festival.get_name(),
                                    "solar_date": festival_date.isoformat(),
                                    "days_left": days_left,
                                }
                            )
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
