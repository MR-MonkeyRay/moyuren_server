"""针对模板布局上下文分支的渲染测试。"""

import re
from pathlib import Path

import pytest

from app.services.compute import DataComputer
from app.services.renderer import ImageRenderer


TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "templates"


def _render(template_name: str, *, legacy: bool = False, **overrides: object) -> str:
    """使用完整且支持回退的上下文渲染正式模板。"""
    # Jinja 渲染只需环境缓存，不需要浏览器或 I/O。
    renderer = ImageRenderer.__new__(ImageRenderer)
    renderer._env_cache = {}
    env = renderer._get_jinja_env(str(TEMPLATES_DIR / template_name))
    data = DataComputer().compute({})
    if legacy:
        data.pop("next_rest_day")
        data.pop("off_work")
    data.update(overrides)
    return env.get_template(template_name).render(data)


@pytest.mark.parametrize("lunar_year", ["丙午", "丙午年"])
def test_cute_lunar_year_has_exactly_one_suffix(lunar_year: str) -> None:
    """接受带或不带中文“年”后缀的农历年份。"""
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
    """优先使用休息日上下文，并显示工作日的下班倒计时。"""
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
    assert "距下班 2 小时 15 分" in html
    assert "周末啦" not in html
    assert "周末愉快" not in html


@pytest.mark.parametrize("template_name", ["moyuren.html", "moyuren_cute.html"])
def test_templates_show_named_rest_day_and_completed_workday(
    template_name: str,
) -> None:
    """提供相应数据时，显示当天法定节假日名称和已下班标记。"""
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
    """缺少新倒计时键的自定义上下文仍渲染旧分支。"""
    html = _render(template_name, legacy=True, weekend={"is_weekend": False, "days_left": 2})

    assert "距周末" in html
    assert "距下班" not in html


@pytest.mark.parametrize("template_name", ["moyuren.html", "moyuren_cute.html"])
def test_templates_hide_work_end_on_rest_days(template_name: str) -> None:
    """实际休息日的上下文隐藏两种下班状态。"""
    html = _render(
        template_name,
        next_rest_day={"is_today_off": True, "today_name": "国庆节"},
        off_work=None,
    )

    assert "国庆节快乐" in html
    assert "距下班" not in html
    assert "已下班" not in html


@pytest.mark.parametrize(
    ("template_name", "badge_class"),
    [
        ("moyuren.html", "off-work-wrap"),
        ("moyuren_cute.html", "off-work-badge font-cute"),
    ],
)
@pytest.mark.parametrize(
    ("off_work", "expected"),
    [
        ({"hours": 9, "minutes": 5, "end_time": "18:00"}, "距下班 9 小时 5 分"),
        ({"hours": 9, "minutes": 0, "end_time": "18:00"}, "距下班 9 小时"),
        ({"hours": 0, "minutes": 45, "end_time": "18:00"}, "距下班 45 分"),
        ({"hours": 0, "minutes": 0, "end_time": "18:00"}, "即将下班"),
        ({"done": True}, "已下班"),
        (None, None),
    ],
)
def test_off_work_text_uses_chinese_units(
    template_name: str,
    badge_class: str,
    off_work: dict[str, object] | None,
    expected: str | None,
) -> None:
    """使用中文单位和现有 CSS 类渲染各个倒计时分支。"""
    html = _render(template_name, off_work=off_work)
    badge = re.search(rf'<div class="{badge_class}">(.*?)</div>', html, re.DOTALL)

    if expected is None:
        assert badge is None
        return

    assert badge is not None
    text = badge.group(1).strip()
    assert text == expected
    assert "h " not in text
    assert "m" not in text
    assert "\n" not in text
