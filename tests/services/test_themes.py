"""Festival precedence and actual solar-term boundaries for template themes."""

from datetime import date, datetime, time, timedelta
from typing import Any
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from tyme4py.festival import LunarFestival
from tyme4py.solar import SolarDay

from app.core.config import TextLimitsConfig, ThemesConfig
from app.services.calendar import CalendarService
from app.services.compute import DataComputer


def _compute(
    day: date, themes: ThemesConfig | None = None, raw_data: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Supply a fixed business snapshot, without external services or clocks."""
    snapshot = datetime.combine(day, time(10), tzinfo=ZoneInfo("Asia/Shanghai"))
    return DataComputer(themes=themes).compute(raw_data or {}, now=snapshot)["theme"]


def _lunar_festival_day(name: str) -> date:
    """Find the actual festival date through the same calendar library."""
    for index in range(13):
        festival = LunarFestival.from_index(2026, index)
        if festival.get_name() == name:
            day = festival.get_day().get_solar_day()
            return date(day.get_year(), day.get_month(), day.get_day())
    pytest.fail(f"Missing lunar festival {name}")


def test_spring_festival_legal_break_precedes_exact_festival() -> None:
    """A legal break selects its original name before display-name merging."""
    today = _lunar_festival_day("春节") - timedelta(days=1)
    assert _compute(today, raw_data={"holidays": [{
        "name": "春节", "start_date": today.isoformat(),
        "end_date": (today + timedelta(days=8)).isoformat(), "is_off_day": True,
    }]}) == {"name": "spring_festival", "source": "春节", "kind": "festival"}


def test_mid_autumn_exact_festival_uses_untruncated_name() -> None:
    """Calendar names contain a date prefix; display limits must not affect matching."""
    today = _lunar_festival_day("中秋节")
    snapshot = datetime.combine(today, time(10), tzinfo=ZoneInfo("Asia/Shanghai"))
    result = DataComputer(text_limits=TextLimitsConfig(holiday_name=1)).compute({}, now=snapshot)
    assert result["theme"] == {"name": "mid_autumn", "source": "中秋节", "kind": "festival"}


@pytest.mark.parametrize("offset", [-1, 0, 1, 14, 15])
def test_spring_festival_lunar_period(offset: int) -> None:
    """除夕 through 正月十五 is inclusive, with normal seasons resuming 正月十六."""
    result = _compute(_lunar_festival_day("春节") + timedelta(days=offset))
    assert result["name"] == ("spring" if offset == 15 else "spring_festival")
    assert result["kind"] == ("season" if offset == 15 else "festival")


@pytest.mark.parametrize(
    ("term_name", "before", "after"),
    [("立春", "winter", "spring"), ("立夏", "spring", "summer"),
     ("立秋", "summer", "autumn"), ("立冬", "autumn", "winter")],
)
def test_real_2026_season_boundaries(term_name: str, before: str, after: str) -> None:
    """Derive real 2026 boundaries via tyme, including the day before and after."""
    term = SolarDay.from_ymd(2026, 1, 1).get_term()
    for _ in range(25):
        day = term.get_julian_day().get_solar_day()
        if term.get_name() == term_name and day.get_year() == 2026:
            break
        term = term.next(1)
    else:
        pytest.fail(f"Missing 2026 term {term_name}")
    boundary = date(day.get_year(), day.get_month(), day.get_day())
    config = ThemesConfig(festival_themes={})
    assert _compute(boundary - timedelta(days=1), config) == {
        "name": before, "source": term.next(-1).get_name(), "kind": "season",
    }
    for offset in (0, 1):
        assert _compute(boundary + timedelta(days=offset), config) == {
            "name": after, "source": term_name, "kind": "season",
        }


def test_january_uses_previous_year_winter_period() -> None:
    """The early-January season uses the current winter term, not the next one."""
    assert _compute(date(2026, 1, 1)) == {
        "name": "winter", "source": "冬至", "kind": "season",
    }


def test_disabled_seasons_fall_back_but_preserve_festivals() -> None:
    """Season opt-out leaves mapped festivals enabled."""
    config = ThemesConfig(season_themes=False)
    assert _compute(date(2026, 6, 1), config) == {
        "name": "default", "source": None, "kind": "default",
    }
    assert _compute(_lunar_festival_day("中秋节"), config)["name"] == "mid_autumn"


def test_disabled_themes_override_legal_and_lunar_matches() -> None:
    """The master switch always yields the default context shape."""
    day = _lunar_festival_day("春节")
    assert _compute(day, ThemesConfig(enabled=False), {"holidays": [{
        "name": "春节", "start_date": day.isoformat(), "end_date": day.isoformat(),
        "is_off_day": True,
    }]}) == {"name": "default", "source": None, "kind": "default"}


def test_custom_unknown_theme_name_is_passed_through() -> None:
    """Valid custom names remain available to user-supplied template CSS."""
    assert _compute(_lunar_festival_day("中秋节"), ThemesConfig(
        festival_themes={"中秋节": "custom_moon_2"},
    )) == {"name": "custom_moon_2", "source": "中秋节", "kind": "festival"}


def test_solar_festival_mapping() -> None:
    """Modern solar festival names use CalendarService without duplicated dates."""
    assert _compute(date(2026, 6, 1), ThemesConfig(
        festival_themes={"儿童节": "custom_children"},
    )) == {"name": "custom_children", "source": "儿童节", "kind": "festival"}


@pytest.mark.parametrize("off_day", [False, "false", "0"])
def test_workday_ranges_do_not_select_a_festival(off_day: bool | str) -> None:
    """Makeup workdays do not acquire the holiday theme just from a range."""
    today = date(2026, 6, 1)
    assert _compute(today, raw_data={"holidays": [{
        "name": "国庆节", "start_date": today.isoformat(),
        "end_date": today.isoformat(), "is_off_day": off_day,
    }]})["name"] == "summer"


def test_legal_range_bounds_are_inclusive_and_malformed_ranges_are_ignored() -> None:
    """Both off-day endpoints match and outside dates return to the seasonal palette."""
    holidays = [None, {"name": "国庆节", "start_date": "invalid"}, {
        "name": "国庆节", "start_date": "2026-06-02", "end_date": "2026-06-04",
        "is_off_day": True,
    }]
    for day in (2, 3, 4):
        assert _compute(date(2026, 6, day), raw_data={"holidays": holidays}) == {
            "name": "national_day", "source": "国庆节", "kind": "festival",
        }
    for day in (1, 5):
        assert _compute(date(2026, 6, day), raw_data={"holidays": holidays})["name"] == "summer"


def test_missing_current_term_falls_back_to_default() -> None:
    """Calendar failure does not invent a season."""
    with patch.object(CalendarService, "get_current_solar_term", return_value=None):
        assert _compute(date(2026, 6, 1)) == {
            "name": "default", "source": None, "kind": "default",
        }


def test_calendar_theme_helpers_degrade_when_tyme_fails() -> None:
    """Theme helpers follow CalendarService's existing fallback contract."""
    with patch("app.services.calendar.SolarDay.from_ymd", side_effect=ValueError("invalid day")):
        assert CalendarService.get_current_solar_term(date(2026, 6, 1)) is None
        assert CalendarService.is_spring_festival_period(date(2026, 6, 1)) is False


def test_spring_festival_extension_honors_custom_mapping_or_opt_out() -> None:
    """The extended period uses the configured 春节 target and disappears if unmapped."""
    day = _lunar_festival_day("春节") + timedelta(days=5)
    assert _compute(day, ThemesConfig(festival_themes={"春节": "custom_new_year"})) == {
        "name": "custom_new_year", "source": "春节", "kind": "festival",
    }
    assert _compute(day, ThemesConfig(festival_themes={})) == {
        "name": "spring", "source": CalendarService.get_current_solar_term(day), "kind": "season",
    }
