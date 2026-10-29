"""Tests for app/services/calendar.py - calendar service."""

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
    """Tests for get_local_timezone function."""

    def test_uses_tz_env_variable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test uses TZ environment variable."""
        monkeypatch.setenv("TZ", "America/New_York")
        tz = get_local_timezone()
        assert str(tz) == "America/New_York"

    def test_invalid_tz_falls_back(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test invalid TZ falls back to local timezone."""
        monkeypatch.setenv("TZ", "Invalid/Zone")
        tz = get_local_timezone()
        # Should not raise, returns some valid timezone
        assert tz is not None


class TestGetTimezoneLabel:
    """Tests for get_timezone_label function."""

    def test_positive_offset(self) -> None:
        """Test positive UTC offset label."""
        dt = datetime(2026, 2, 4, 10, 0, 0, tzinfo=timezone(timedelta(hours=8)))
        label = get_timezone_label(dt)
        assert label == "UTC+08"

    def test_negative_offset(self) -> None:
        """Test negative UTC offset label."""
        dt = datetime(2026, 2, 4, 10, 0, 0, tzinfo=timezone(timedelta(hours=-5)))
        label = get_timezone_label(dt)
        assert label == "UTC-05"

    def test_zero_offset(self) -> None:
        """Test zero UTC offset label."""
        dt = datetime(2026, 2, 4, 10, 0, 0, tzinfo=UTC)
        label = get_timezone_label(dt)
        assert label == "UTC+00"

    def test_offset_with_minutes(self) -> None:
        """Test offset with minutes."""
        dt = datetime(2026, 2, 4, 10, 0, 0, tzinfo=timezone(timedelta(hours=5, minutes=30)))
        label = get_timezone_label(dt)
        assert label == "UTC+05:30"


class TestInitTimezones:
    """Tests for init_timezones function."""

    def test_init_with_iana_timezones(self) -> None:
        """Test initialization with IANA timezone names."""
        init_timezones("Asia/Shanghai", "America/New_York")
        biz_tz = get_business_timezone()
        assert str(biz_tz) == "Asia/Shanghai"

    def test_init_with_local_display(self) -> None:
        """Test initialization with 'local' display timezone."""
        init_timezones("Asia/Shanghai", "local")
        display_tz = get_display_timezone()
        # Should return some valid timezone
        assert display_tz is not None

    def test_init_with_utc_offset(self) -> None:
        """Test initialization with UTC offset format."""
        init_timezones("UTC+8", "UTC-5")
        biz_tz = get_business_timezone()
        # Should be equivalent to +08:00
        assert biz_tz is not None


class TestGetBusinessTimezone:
    """Tests for get_business_timezone function."""

    def test_returns_initialized_timezone(self) -> None:
        """Test returns initialized timezone."""
        init_timezones("Asia/Tokyo", "local")
        tz = get_business_timezone()
        assert str(tz) == "Asia/Tokyo"


class TestNowFunctions:
    """Tests for now_business and today_business functions."""

    def test_now_business_returns_datetime(self) -> None:
        """Test now_business returns datetime with timezone."""
        init_timezones("Asia/Shanghai", "local")
        now = now_business()
        assert now.tzinfo is not None

    def test_today_business_returns_date(self) -> None:
        """Test today_business returns date."""
        init_timezones("Asia/Shanghai", "local")
        today = today_business()
        assert isinstance(today, date)


class TestCalendarService:
    """Tests for CalendarService class."""

    def test_get_lunar_info(self) -> None:
        """Test get lunar calendar info."""
        today = date(2026, 2, 4)
        info = CalendarService.get_lunar_info(today)

        assert "lunar_year" in info
        assert "lunar_date" in info
        assert "zodiac" in info

    def test_get_festivals(self) -> None:
        """Test get festivals for a date."""
        # Test a known festival date (New Year's Day)
        new_year = date(2026, 1, 1)
        festivals = CalendarService.get_festivals(new_year)

        assert "festival_solar" in festivals
        assert "festival_lunar" in festivals
        assert "legal_holiday" in festivals

    def test_get_constellation(self) -> None:
        """Test get constellation for a date."""
        # February 4 is Aquarius
        today = date(2026, 2, 4)
        constellation = CalendarService.get_constellation(today)

        assert constellation == "水瓶座"

    def test_get_moon_phase(self) -> None:
        """Test get moon phase for a date."""
        today = date(2026, 2, 4)
        phase = CalendarService.get_moon_phase(today)

        assert phase is not None
        assert isinstance(phase, str)

    def test_is_holiday(self) -> None:
        """Test is_holiday check."""
        # Test a known holiday (New Year's Day)
        new_year = date(2026, 1, 1)
        is_holiday = CalendarService.is_holiday(new_year)

        # New Year's Day should be a holiday
        assert isinstance(is_holiday, bool)

    @pytest.mark.parametrize(
        ("dt", "expected"),
        [(date(2026, 2, 14), False), (date(2026, 2, 17), True), (date(2026, 3, 12), False)],
    )
    def test_is_holiday_excludes_makeup_work_days(self, dt: date, expected: bool) -> None:
        """Test legal holidays exclude makeup work days and ordinary festivals."""
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
        """Test lunar festivals span the lunar year boundary and remain unique and sorted."""
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
        """Test late December includes the next January's solar festival."""
        festivals = CalendarService.get_upcoming_solar_festivals(date(2026, 12, 31))

        assert festivals[0] == {"name": "元旦", "solar_date": "2027-01-01", "days_left": 1}
        dates = [festival["solar_date"] for festival in festivals]
        names = [festival["name"] for festival in festivals]
        assert dates == sorted(dates)
        assert len(names) == len(set(names))

    def test_get_solar_term_info(self) -> None:
        """Test get solar term info."""
        # February 4 is typically around Lichun (Beginning of Spring)
        today = date(2026, 2, 4)
        info = CalendarService.get_solar_term_info(today)

        assert "name" in info
        assert "name_en" in info
        assert "date" in info
        assert "days_left" in info
        assert "is_today" in info

    def test_get_solar_term_info_is_today(self) -> None:
        """Test solar term is_today flag."""
        # Find a date that is a solar term
        # Lichun 2026 is around Feb 4
        lichun_date = date(2026, 2, 4)
        info = CalendarService.get_solar_term_info(lichun_date)

        # The is_today flag should be boolean
        assert isinstance(info["is_today"], bool)
