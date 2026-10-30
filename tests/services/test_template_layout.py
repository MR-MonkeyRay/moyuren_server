"""Focused rendering tests for template layout context branches."""

from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader

from app.services.compute import DataComputer
from app.services.renderer import format_datetime


TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "templates"


def _render(template_name: str, *, legacy: bool = False, **overrides: object) -> str:
    """Render a production template from a complete fallback-safe context."""
    env = Environment(loader=FileSystemLoader(TEMPLATES_DIR), autoescape=True)
    env.filters["format_datetime"] = format_datetime
    data = DataComputer().compute({})
    if legacy:
        data.pop("next_rest_day")
        data.pop("off_work")
    data.update(overrides)
    return env.get_template(template_name).render(data)


@pytest.mark.parametrize("lunar_year", ["丙午", "丙午年"])
def test_cute_lunar_year_has_exactly_one_suffix(lunar_year: str) -> None:
    """Accept lunar-year values with or without their Chinese year suffix."""
    data = DataComputer().compute({})
    date = {**data["date"], "lunar_year": lunar_year}

    html = _render("moyuren_cute.html", date=date)

    assert "丙午年" in html
    assert "年年" not in html


@pytest.mark.parametrize(
    ("template_name", "countdown"),
    [("moyuren.html", "距休息日还有"), ("moyuren_cute.html", "距休息 3 天")],
)
def test_templates_prefer_next_rest_day_and_show_off_work(
    template_name: str, countdown: str
) -> None:
    """Use the rest-day-aware context and render the workday countdown."""
    html = _render(
        template_name,
        weekend={"is_weekend": True, "days_left": 0},
        next_rest_day={
            "date": "2026-05-06",
            "days_left": 3,
            "is_today_off": False,
            "name": "劳动节",
            "today_name": None,
        },
        off_work={"hours": 2, "minutes": 15, "end_time": "18:00"},
    )

    assert countdown in html
    assert "距下班 2h 15m" in html
    assert "周末啦" not in html
    assert "周末愉快" not in html


@pytest.mark.parametrize("template_name", ["moyuren.html", "moyuren_cute.html"])
def test_templates_show_named_rest_day_and_completed_workday(
    template_name: str,
) -> None:
    """Show today's statutory name and the completed-work badge when supplied."""
    html = _render(
        template_name,
        next_rest_day={
            "date": "2026-10-01",
            "days_left": 0,
            "is_today_off": True,
            "name": "国庆节",
            "today_name": "国庆节",
        },
        off_work={"done": True},
    )

    assert "国庆节快乐" in html
    assert "已下班" in html


@pytest.mark.parametrize("template_name", ["moyuren.html", "moyuren_cute.html"])
def test_templates_keep_legacy_weekend_context(template_name: str) -> None:
    """Custom contexts without new countdown keys still render the old branch."""
    html = _render(template_name, legacy=True, weekend={"is_weekend": False, "days_left": 2})

    assert "距周末" in html
    assert "距下班" not in html


@pytest.mark.parametrize("template_name", ["moyuren.html", "moyuren_cute.html"])
def test_templates_hide_work_end_on_rest_days(template_name: str) -> None:
    """An actual off-day context hides both work-end states."""
    html = _render(
        template_name,
        next_rest_day={"is_today_off": True, "today_name": "国庆节"},
        off_work=None,
    )

    assert "国庆节快乐" in html
    assert "距下班" not in html
    assert "已下班" not in html
