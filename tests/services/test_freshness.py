"""Tests for data freshness computation and presentation."""

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from app.api.v1.moyuren import _build_detail_response
from app.core.config import TemplateRenderConfig, TemplatesConfig
from app.models.schemas import DailyDataFile
from app.services.compute import DataComputer
from app.services.renderer import ImageRenderer


NOW = datetime(2026, 3, 12, 10, tzinfo=timezone(timedelta(hours=8)))


def _raw_news(date_value: str) -> dict[str, object]:
    """Build the minimal raw news payload."""
    return {"news": {"data": {"date": date_value, "news": []}}}


@pytest.mark.parametrize("date_value", ["2026-03-12", "2026/03/12", "2026年3月12日"])
def test_compute_marks_current_news_fresh(date_value: str) -> None:
    """Recognize all supported current-date formats."""
    freshness = DataComputer().compute(_raw_news(date_value), now=NOW)["data_freshness"]

    assert freshness == {
        "is_stale": False,
        "news_is_stale": False,
        "source_date": "2026-03-12",
        "stale_sources": [],
    }


def test_compute_marks_old_news_stale() -> None:
    """Mark news for another business date as stale."""
    freshness = DataComputer().compute(_raw_news("2026-03-11"), now=NOW)["data_freshness"]

    assert freshness["is_stale"] is True
    assert freshness["news_is_stale"] is True
    assert freshness["source_date"] == "2026-03-11"


@pytest.mark.parametrize("raw_data", [{}, {"news": []}, {"news": {"data": {"date": "bad"}}}])
def test_compute_unknown_news_date_is_not_stale(raw_data: dict[str, object]) -> None:
    """Do not infer staleness when the news date cannot be determined."""
    freshness = DataComputer().compute(raw_data, now=NOW)["data_freshness"]

    assert freshness == {
        "is_stale": False,
        "news_is_stale": False,
        "source_date": None,
        "stale_sources": [],
    }


def test_compute_propagates_stale_sources() -> None:
    """Propagate cache fallback sources and deduplicate them."""
    raw_data = _raw_news("2026-03-12")
    raw_data["stale_sources"] = ["news", "gold_price", "news"]

    freshness = DataComputer().compute(raw_data, now=NOW)["data_freshness"]

    assert freshness == {
        "is_stale": True,
        "news_is_stale": False,
        "source_date": "2026-03-12",
        "stale_sources": ["news", "gold_price"],
    }


def test_daily_data_file_round_trip_freshness(tmp_path: Path) -> None:
    """Persist freshness fields while keeping legacy files compatible."""
    base = {
        "date": "2026-03-12",
        "updated": "2026/03/12 10:00:00",
        "updated_at": 1773309600000,
        "images": {"moyuren": "moyuren.jpg"},
    }
    target = tmp_path / "data.json"
    DailyDataFile.model_validate(
        {**base, "is_stale": True, "source_date": "2026-03-11", "stale_sources": ["news"]}
    ).save_atomic(target)

    loaded = DailyDataFile.load(target)
    assert loaded.is_stale is True
    assert loaded.source_date == "2026-03-11"
    assert loaded.stale_sources == ["news"]

    legacy_target = tmp_path / "legacy.json"
    DailyDataFile.model_validate(base).save_atomic(legacy_target)
    legacy = DailyDataFile.load(legacy_target)
    assert legacy.is_stale is None
    assert legacy.source_date is None
    assert legacy.stale_sources is None
    assert "is_stale" not in legacy.model_dump(exclude_unset=True)


def test_detail_response_includes_freshness_and_legacy_defaults() -> None:
    """Expose stored freshness and default legacy data to fresh."""
    base = {
        "date": "2026-03-12",
        "updated": "2026/03/12 10:00:00",
        "updated_at": 1773309600000,
        "images": {"moyuren": "moyuren.jpg"},
    }
    stale = _build_detail_response(
        DailyDataFile.model_validate({**base, "is_stale": True, "source_date": "2026-03-11"}),
        "https://example.com",
        None,
    )
    legacy = _build_detail_response(
        DailyDataFile.model_validate({**base, "is_stale": None}), "https://example.com", None
    )

    assert stale["is_stale"] is True
    assert stale["source_date"] == "2026-03-11"
    assert legacy["is_stale"] is False
    assert legacy["source_date"] is None


@pytest.mark.parametrize("template_name", ["moyuren.html", "moyuren_cute.html"])
def test_template_freshness_marker(template_name: str, tmp_path: Path) -> None:
    """Render a marker only for stale template contexts."""
    templates_dir = Path(__file__).resolve().parents[2] / "templates"
    renderer = ImageRenderer(
        templates_config=TemplatesConfig(),
        images_dir=str(tmp_path / "images"),
        render_config=TemplateRenderConfig(),
        logger=logging.getLogger(__name__),
    )
    env = renderer._get_jinja_env(str(templates_dir / template_name))
    template = env.get_template(template_name)
    base = DataComputer().compute(_raw_news("2026-03-12"), now=NOW)

    stale_html = template.render(
        {
            **base,
            "data_freshness": {
                "is_stale": True,
                "news_is_stale": True,
                "source_date": "2026-03-11",
            },
        }
    )
    # 当日新闻 + 其他数据源降级：不能把当日日期标成"非当日"
    cache_only_html = template.render(
        {
            **base,
            "data_freshness": {
                "is_stale": True,
                "news_is_stale": False,
                "source_date": "2026-03-12",
            },
        }
    )
    fresh_html = template.render(base)
    missing_html = template.render({key: value for key, value in base.items() if key != "data_freshness"})

    assert "数据日期 2026-03-11（非当日）" in stale_html
    assert "部分数据为缓存" in cache_only_html
    assert "数据日期" not in cache_only_html
    assert "数据日期" not in fresh_html
    assert "部分数据为缓存" not in fresh_html
    assert "数据日期" not in missing_html
    assert "部分数据为缓存" not in missing_html


@pytest.mark.parametrize("template_name", ["moyuren.html", "moyuren_cute.html"])
def test_template_unknown_stale_date_marker(template_name: str, tmp_path: Path) -> None:
    """Render the cache marker when a stale source date is unknown."""
    templates_dir = Path(__file__).resolve().parents[2] / "templates"
    renderer = ImageRenderer(
        templates_config=TemplatesConfig(),
        images_dir=str(tmp_path / "images"),
        render_config=TemplateRenderConfig(),
        logger=logging.getLogger(__name__),
    )
    env = renderer._get_jinja_env(str(templates_dir / template_name))
    data = DataComputer().compute({}, now=NOW)
    data["data_freshness"] = {"is_stale": True, "source_date": None}

    assert "部分数据为缓存" in env.get_template(template_name).render(data)
