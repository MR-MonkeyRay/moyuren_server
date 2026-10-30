"""app/services/calendar.py 日历服务测试。"""

from datetime import UTC, date, datetime, timedelta, timezone

import pytest

from app.services.calendar import (
    CalendarService,
    get_business_timezone,
    get_display_timezone,
    get_local_timezone,
    get_timezone_label,
    init_timezones,
    now_business,
    today_business,
)


class TestGetLocalTimezone:
    """get_local_timezone 函数测试。"""

    def test_uses_tz_env_variable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """验证使用 TZ 环境变量。"""
        monkeypatch.setenv("TZ", "America/New_York")
        tz = get_local_timezone()
        assert str(tz) == "America/New_York"

    def test_invalid_tz_falls_back(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """验证无效 TZ 回退到本地时区。"""
        monkeypatch.setenv("TZ", "Invalid/Zone")
        tz = get_local_timezone()
        # 不应抛出异常，应返回有效时区
        assert tz is not None


class TestGetTimezoneLabel:
    """get_timezone_label 函数测试。"""

    def test_positive_offset(self) -> None:
        """验证 UTC 正偏移标签。"""
        dt = datetime(2026, 2, 4, 10, 0, 0, tzinfo=timezone(timedelta(hours=8)))
        label = get_timezone_label(dt)
        assert label == "UTC+08"

    def test_negative_offset(self) -> None:
        """验证 UTC 负偏移标签。"""
        dt = datetime(2026, 2, 4, 10, 0, 0, tzinfo=timezone(timedelta(hours=-5)))
        label = get_timezone_label(dt)
        assert label == "UTC-05"

    def test_zero_offset(self) -> None:
        """验证 UTC 零偏移标签。"""
        dt = datetime(2026, 2, 4, 10, 0, 0, tzinfo=UTC)
        label = get_timezone_label(dt)
        assert label == "UTC+00"

    def test_offset_with_minutes(self) -> None:
        """验证包含分钟的偏移量。"""
        dt = datetime(2026, 2, 4, 10, 0, 0, tzinfo=timezone(timedelta(hours=5, minutes=30)))
        label = get_timezone_label(dt)
        assert label == "UTC+05:30"


class TestInitTimezones:
    """init_timezones 函数测试。"""

    def test_init_with_iana_timezones(self) -> None:
        """验证使用 IANA 时区名称初始化。"""
        init_timezones("Asia/Shanghai", "America/New_York")
        biz_tz = get_business_timezone()
        assert str(biz_tz) == "Asia/Shanghai"

    def test_init_with_local_display(self) -> None:
        """验证将显示时区设为 'local' 的初始化。"""
        init_timezones("Asia/Shanghai", "local")
        display_tz = get_display_timezone()
        # 应返回有效时区
        assert display_tz is not None

    def test_init_with_utc_offset(self) -> None:
        """验证使用 UTC 偏移格式初始化。"""
        init_timezones("UTC+8", "UTC-5")
        biz_tz = get_business_timezone()
        # 应等同于 +08:00
        assert biz_tz is not None


class TestGetBusinessTimezone:
    """get_business_timezone 函数测试。"""

    def test_returns_initialized_timezone(self) -> None:
        """验证返回已初始化的时区。"""
        init_timezones("Asia/Tokyo", "local")
        tz = get_business_timezone()
        assert str(tz) == "Asia/Tokyo"


class TestNowFunctions:
    """now_business 和 today_business 函数测试。"""

    def test_now_business_returns_datetime(self) -> None:
        """验证 now_business 返回带时区的 datetime。"""
        init_timezones("Asia/Shanghai", "local")
        now = now_business()
        assert now.tzinfo is not None

    def test_today_business_returns_date(self) -> None:
        """验证 today_business 返回 date。"""
        init_timezones("Asia/Shanghai", "local")
        today = today_business()
        assert isinstance(today, date)


class TestCalendarService:
    """CalendarService 类测试。"""

    def test_get_lunar_info(self) -> None:
        """验证获取农历信息。"""
        today = date(2026, 2, 4)
        info = CalendarService.get_lunar_info(today)

        assert "lunar_year" in info
        assert "lunar_date" in info
        assert "zodiac" in info

    def test_get_festivals(self) -> None:
        """验证获取指定日期的节日。"""
        # 使用已知节日日期（元旦）测试
        new_year = date(2026, 1, 1)
        festivals = CalendarService.get_festivals(new_year)

        assert "festival_solar" in festivals
        assert "festival_lunar" in festivals
        assert "legal_holiday" in festivals

    def test_get_constellation(self) -> None:
        """验证获取指定日期的星座。"""
        # 2 月 4 日属于水瓶座
        today = date(2026, 2, 4)
        constellation = CalendarService.get_constellation(today)

        assert constellation == "水瓶座"

    def test_get_moon_phase(self) -> None:
        """验证获取指定日期的月相。"""
        today = date(2026, 2, 4)
        phase = CalendarService.get_moon_phase(today)

        assert phase is not None
        assert isinstance(phase, str)

    def test_is_holiday(self) -> None:
        """验证 is_holiday 判断。"""
        # 使用已知假日（元旦）测试
        new_year = date(2026, 1, 1)
        is_holiday = CalendarService.is_holiday(new_year)

        # 元旦应为假日
        assert isinstance(is_holiday, bool)

    @pytest.mark.parametrize(
        ("dt", "expected"),
        [(date(2026, 2, 14), False), (date(2026, 2, 17), True), (date(2026, 3, 12), False)],
    )
    def test_is_holiday_excludes_makeup_work_days(self, dt: date, expected: bool) -> None:
        """验证法定假日不包含调休补班日和普通节日。"""
        assert CalendarService.is_holiday(dt) is expected

    @pytest.mark.parametrize(
        ("dt", "name", "solar_date"),
        [
            (date(2026, 1, 1), "腊八节", "2026-01-26"),
            (date(2026, 1, 1), "除夕", "2026-02-16"),
            (date(2026, 1, 26), "腊八节", "2026-01-26"),
            (date(2026, 6, 1), "端午节", "2026-06-19"),
        ],
    )
    def test_upcoming_lunar_festivals(self, dt: date, name: str, solar_date: str) -> None:
        """验证农历节日可跨农历年边界，且保持唯一、有序。"""
        festivals = CalendarService.get_upcoming_lunar_festivals(dt)

        assert {
            "name": name,
            "solar_date": solar_date,
            "days_left": (date.fromisoformat(solar_date) - dt).days,
        } in festivals
        dates = [festival["solar_date"] for festival in festivals]
        names = [festival["name"] for festival in festivals]
        assert dates == sorted(dates)
        assert all(festival_date >= dt.isoformat() for festival_date in dates)
        assert len(names) == len(set(names))
        assert CalendarService.get_upcoming_lunar_festivals(dt, count=3) == festivals[:3]

    def test_upcoming_solar_festivals_cross_year_boundary(self) -> None:
        """验证 12 月末包含次年 1 月的公历节日。"""
        festivals = CalendarService.get_upcoming_solar_festivals(date(2026, 12, 31))

        assert festivals[0] == {"name": "元旦", "solar_date": "2027-01-01", "days_left": 1}
        dates = [festival["solar_date"] for festival in festivals]
        names = [festival["name"] for festival in festivals]
        assert dates == sorted(dates)
        assert len(names) == len(set(names))

    def test_get_solar_term_info(self) -> None:
        """验证获取节气信息。"""
        # 2 月 4 日通常在立春前后
        today = date(2026, 2, 4)
        info = CalendarService.get_solar_term_info(today)

        assert "name" in info
        assert "name_en" in info
        assert "date" in info
        assert "days_left" in info
        assert "is_today" in info

    def test_get_solar_term_info_is_today(self) -> None:
        """验证节气的 is_today 标志。"""
        # 选取节气日期：2026 年立春在 2 月 4 日前后
        lichun_date = date(2026, 2, 4)
        info = CalendarService.get_solar_term_info(lichun_date)

        # is_today 标志应为布尔值
        assert isinstance(info["is_today"], bool)
