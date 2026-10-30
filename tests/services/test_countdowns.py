"""休息日、下班倒计时及假期内节日计算的回归测试。"""

from datetime import date, datetime, timedelta, timezone
from typing import Any
from unittest.mock import patch

import pytest

from app.services.calendar import CalendarService
from app.services.compute import DataComputer, DomainDataAggregator


def _snapshot(value: str) -> datetime:
    """构造不依赖系统时钟的固定业务时间快照。"""
    return datetime.fromisoformat(value).replace(tzinfo=timezone(timedelta(hours=8)))


@pytest.mark.parametrize(
    ("today", "expected_date", "days_left", "today_off", "name"),
    [
        ("2026-02-13T10:00", "2026-02-15", 2, False, "春节"),
        ("2026-02-14T10:00", "2026-02-15", 1, False, "春节"),
        ("2026-09-30T10:00", "2026-10-01", 1, False, "国庆节"),
        ("2026-10-01T10:00", "2026-10-02", 1, True, "国庆节"),
        ("2026-05-08T10:00", "2026-05-10", 2, False, None),
    ],
)
def test_rest_days_follow_2026_adjustments(
    today: str, expected_date: str, days_left: int, today_off: bool, name: str | None
) -> None:
    """原始假期区间未涵盖的未来补班周六仍应为工作日。"""
    raw = {
        "holidays": [
            {"name": "国庆节", "start_date": "2026-10-01", "end_date": "2026-10-07"}
        ]
    }
    result = DataComputer().compute(raw, now=_snapshot(today))
    rest = result["next_rest_day"]

    assert rest["date"] == expected_date
    assert rest["days_left"] == days_left
    assert rest["is_today_off"] is today_off
    assert rest["name"] == name
    assert rest["today_name"] == ("国庆节" if today_off else None)
    if today == "2026-02-14T10:00":
        assert result["weekend"] == {"days_left": 0, "is_weekend": True}
        assert result["off_work"] == {"hours": 8, "minutes": 0, "end_time": "18:00"}


def test_raw_off_day_range_wins_over_calendar_makeup_day() -> None:
    """较新的原始数据源在休假区间内优先于 tyme。"""
    raw = {
        "make_up_workdays": ["2026-05-08", "2026-05-09"],
        "holidays": [
            {
                "name": "特别假期",
                "start_date": "2026-05-08",
                "end_date": "2026-05-09",
                "is_off_day": True,
            }
        ]
    }
    with patch.object(CalendarService, "get_day_off_status", return_value=(False, None)):
        rest = DomainDataAggregator()._compute_next_rest_day(_snapshot("2026-05-08T10:00"), raw)

    assert rest == {
        "date": "2026-05-09",
        "days_left": 1,
        "is_today_off": True,
        "name": "特别假期",
        "today_name": "特别假期",
    }


@pytest.mark.parametrize(
    ("today", "days_left"), [("2027-01-08T10:00", 2), ("2027-01-09T10:00", 1)]
)
def test_holiday_cn_makeup_weekend_overrides_outdated_calendar(
    today: str, days_left: int
) -> None:
    """跳过 tyme 尚未收录的补班周六，并显示下班倒计时。"""
    raw = {"holidays": [], "make_up_workdays": ["2027-01-09"]}
    with patch.object(CalendarService, "get_day_off_status",
                      side_effect=lambda target: (target.weekday() >= 5, None)):
        result = DataComputer().compute(raw, now=_snapshot(today))

    assert result["next_rest_day"] == {
        "date": "2027-01-10", "days_left": days_left,
        "is_today_off": False, "name": None, "today_name": None,
    }
    assert result["off_work"] == {"hours": 8, "minutes": 0, "end_time": "18:00"}


@pytest.mark.parametrize("make_up_workdays", [None, [], "2027-01-09", [None, {}]])
def test_missing_or_invalid_makeup_data_preserves_weekend(
    make_up_workdays: Any,
) -> None:
    """旧格式及格式错误的补充数据保留现有周末规则。"""
    with patch.object(CalendarService, "get_day_off_status",
                      side_effect=lambda target: (target.weekday() >= 5, None)):
        result = DataComputer().compute(
            {"holidays": [], "make_up_workdays": make_up_workdays},
            now=_snapshot("2027-01-09T10:00"),
        )
    assert result["next_rest_day"]["is_today_off"] is True
    assert result["off_work"] is None


def test_raw_off_day_range_keeps_off_work_hidden_despite_makeup_data() -> None:
    """休假区间的优先级同样决定今日标记和下班倒计时。"""
    with patch.object(CalendarService, "get_day_off_status", return_value=(False, None)):
        result = DataComputer().compute({
            "holidays": [{"name": "特别假期", "start_date": "2027-01-09", "is_off_day": True}],
            "make_up_workdays": ["2027-01-09"],
        }, now=_snapshot("2027-01-09T10:00"))
    assert result["next_rest_day"]["is_today_off"] is True
    assert result["next_rest_day"]["today_name"] == "特别假期"
    assert result["off_work"] is None


def test_raw_non_off_entry_defers_to_calendar() -> None:
    """只有原始休假区间能覆盖日历的法定休假状态。"""
    raw = {"holidays": [{
        "name": "旧补班安排",
        "start_date": "2026-05-09",
        "is_off_day": False,
    }]}
    with patch.object(CalendarService, "get_day_off_status", return_value=(True, "更新后的假期")):
        rest = DomainDataAggregator()._compute_next_rest_day(_snapshot("2026-05-09T10:00"), raw)

    assert rest["is_today_off"] is True
    assert rest["today_name"] == "更新后的假期"


@pytest.mark.parametrize("raw", [{}, {"holidays": None}])
def test_missing_holidays_fall_back_to_plain_weekend(raw: dict[str, Any]) -> None:
    """缺少节假日数据时，补班周六也沿用原有周末规则。"""
    with patch.object(CalendarService, "get_day_off_status") as status:
        rest = DomainDataAggregator()._compute_next_rest_day(_snapshot("2026-02-13T10:00"), raw)

    assert rest["date"] == "2026-02-14"
    assert rest["days_left"] == 1
    assert rest["name"] is None
    status.assert_not_called()


def test_rest_day_search_is_bounded() -> None:
    """异常工作安排下搜索仍会结束，并回退到普通周六。"""
    with patch.object(CalendarService, "get_day_off_status", return_value=(False, None)) as status:
        rest = DomainDataAggregator()._compute_next_rest_day(
            _snapshot("2026-05-08T10:00"), {"holidays": []}
        )

    assert status.call_count == 32  # 今日状态 + 最多 31 个未来日期
    assert rest["date"] == "2026-05-09"


@pytest.mark.parametrize(
    ("snapshot", "end_time", "expected"),
    [
        ("2026-05-08T16:35", "18:00", {"hours": 1, "minutes": 25, "end_time": "18:00"}),
        ("2026-05-08T18:00", "18:00", {"done": True}),
        ("2026-05-08T19:00", "18:00", {"done": True}),
        ("2026-05-08T17:45", "19:15", {"hours": 1, "minutes": 30, "end_time": "19:15"}),
        ("2026-05-08T17:59:59", "18:00", {"hours": 0, "minutes": 1, "end_time": "18:00"}),
        ("2026-05-10T10:00", "18:00", None),
        ("2026-10-01T10:00", "18:00", None),
    ],
)
def test_off_work_uses_shared_snapshot_and_rest_status(
    snapshot: str, end_time: str, expected: dict[str, Any] | None
) -> None:
    """下班倒计时使用配置时间，休息日隐藏，不足一分钟向上取整。"""
    result = DataComputer(work_end_time=end_time).compute(
        {"holidays": []}, now=_snapshot(snapshot)
    )

    assert result["off_work"] == expected


@pytest.mark.parametrize("kind", ["solar", "lunar"])
def test_contained_festival_merges_before_limit_and_refills_slot(kind: str) -> None:
    """青年节或除夕合并到所属法定假期行，并为后续节日腾出位置。"""
    if kind == "solar":
        start, end, holiday_name, festival_name, festival_date = (
            "2026-05-01", "2026-05-05", "劳动节", "青年节", "2026-05-04"
        )
    else:
        start, end, holiday_name, festival_name, festival_date = (
            "2026-02-15", "2026-02-23", "春节", "除夕", "2026-02-16"
        )
    snapshot = _snapshot(f"{start}T10:00")
    festivals = [{"name": festival_name, "solar_date": festival_date, "days_left": 1}]
    for index in range(10):
        upcoming = date.fromisoformat(end) + timedelta(days=index + 1)
        festivals.append({
            "name": f"后续节日{index}",
            "solar_date": upcoming.isoformat(),
            "days_left": (upcoming - snapshot.date()).days,
        })
    raw = {"holidays": [{"name": holiday_name, "start_date": start, "end_date": end}]}
    with (
        patch.object(CalendarService, "get_upcoming_solar_festivals", return_value=festivals if kind == "solar" else []),
        patch.object(CalendarService, "get_upcoming_lunar_festivals", return_value=festivals if kind == "lunar" else []),
    ):
        result = DomainDataAggregator()._compute_holidays(snapshot, raw)

    assert len(result) == 10
    assert result[0]["name"] == f"{holiday_name} · {festival_name}"
    assert all(row["name"] != festival_name for row in result)
    assert result[-1]["name"] == "后续节日8"


def test_makeup_entry_does_not_absorb_same_day_festival() -> None:
    """非休假条目不能合并无关节日。"""
    with (
        patch.object(CalendarService, "get_upcoming_lunar_festivals", return_value=[]),
        patch.object(CalendarService, "get_upcoming_solar_festivals", return_value=[
            {"name": "青年节", "solar_date": "2026-05-04", "days_left": 0}
        ]),
    ):
        result = DomainDataAggregator()._compute_holidays(_snapshot("2026-05-04T10:00"), {
            "holidays": [{"name": "补班", "start_date": "2026-05-04", "is_off_day": False}]
        })

    assert {row["name"] for row in result} == {"补班", "青年节"}
