"""Tests for offline template preview data assembly."""

import logging
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

from app.core.config import TemplateRenderConfig, TemplatesConfig
from app.core.services import AppServices
from app.services.compute import DataComputer
from app.services.renderer import ImageRenderer
from app.services.template_discovery import TemplateDiscovery
from app.services.preview_sample import cached_raw_data, sample_raw_data


def _snapshot() -> datetime:
    return datetime(2026, 4, 9, 9, tzinfo=ZoneInfo("Asia/Shanghai"))


def _cache(value: Any) -> Any:
    return SimpleNamespace(load_cache=MagicMock(return_value=value))


def test_sample_raw_data_is_deterministic_and_complete() -> None:
    """The offline sample covers all optional template data sources."""
    first = sample_raw_data(_snapshot())
    second = sample_raw_data(_snapshot())

    assert first == second
    assert set(first) == {
        "news",
        "holidays",
        "make_up_workdays",
        "fun_content",
        "kfc_copy",
        "stock_indices",
        "gold_price",
        "daily_english",
        "stale_sources",
    }
    assert len(first["news"]["data"]["news"]) == 4
    assert first["news"]["data"]["date"] == "2026-04-09"
    assert first["holidays"]
    assert first["stock_indices"]["items"]
    assert first["daily_english"]["word"] == "balance"


def test_cached_raw_data_reads_only_disk_caches_and_falls_back_per_key(
    mock_services: AppServices,
) -> None:
    """Valid cache values replace samples while missing or malformed values do not."""
    news_service = _cache({
        "news": {"data": {"date": "2026-04-09", "news": ["缓存新闻"]}},
        "weather": {"temperature": 24},
        "broken": None,
    })
    news_service.get = MagicMock(side_effect=AssertionError("network path called"))
    mock_services.data_fetcher = cast(Any, news_service)
    mock_services.holiday_service = cast(Any, _cache({
        "holidays": [{"name": "缓存假期"}],
        "make_up_workdays": ["2026-04-11"],
    }))
    mock_services.fun_content_service = cast(Any, _cache("wrong top type"))
    mock_services.kfc_service = cast(Any, _cache("缓存疯狂星期四"))
    mock_services.gold_price_service = cast(Any, _cache(None))
    mock_services.daily_english_service = cast(Any, _cache({"word": "cached"}))
    mock_services.stock_index_service = cast(
        Any, SimpleNamespace(fetch_indices=MagicMock(side_effect=AssertionError("HTTP called")))
    )

    result = cached_raw_data(mock_services, _snapshot(), logging.getLogger("test.preview"))

    assert result["news"]["data"]["news"] == ["缓存新闻"]
    assert result["weather"] == {"temperature": 24}
    assert "broken" not in result
    assert result["holidays"] == [{"name": "缓存假期"}]
    assert result["make_up_workdays"] == ["2026-04-11"]
    assert result["fun_content"]["title"] == "🐟 摸鱼小贴士"
    assert result["kfc_copy"] == "缓存疯狂星期四"
    assert result["gold_price"]["today_price"] == 786.50
    assert result["daily_english"] == {"word": "cached"}
    assert result["stock_indices"]["items"][0]["name"] == "上证指数"
    news_service.get.assert_not_called()
    mock_services.stock_index_service.fetch_indices.assert_not_called()


def test_cached_raw_data_handles_invalid_news_and_unicode_cache_errors(
    mock_services: AppServices,
    caplog: Any,
) -> None:
    """Unreadable cache files are isolated and replaced by source-specific samples."""
    unreadable = SimpleNamespace(
        load_cache=MagicMock(side_effect=UnicodeDecodeError("utf-8", b"x", 0, 1, "bad"))
    )
    mock_services.data_fetcher = cast(Any, _cache(["invalid news cache"]))
    mock_services.holiday_service = cast(Any, unreadable)
    mock_services.fun_content_service = None
    mock_services.kfc_service = None
    mock_services.gold_price_service = None
    mock_services.daily_english_service = None

    with caplog.at_level(logging.WARNING):
        result = cached_raw_data(
            mock_services, _snapshot(), logging.getLogger("test.preview.unicode")
        )

    assert result["news"]["data"]["news"][0] == "城市公园新增午间休息区"
    assert result["holidays"][0]["name"] == "周末休息日"
    assert "Failed to load preview cache for holidays" in caplog.text


def test_samples_compute_and_render_both_bundled_templates(tmp_path: Path) -> None:
    """Offline samples satisfy the real computer and both Jinja templates."""
    render_config = TemplateRenderConfig()
    items = TemplateDiscovery().discover("templates", render_config)
    renderer = ImageRenderer(
        TemplatesConfig(items=items), str(tmp_path / "images"),
        render_config, logging.getLogger("test.preview.templates"),
    )
    now = _snapshot()
    context = DataComputer().compute(sample_raw_data(now), now=now)
    assert context["news_list"] and context["stock_indices"]["indices"]
    assert context["daily_english"]["word"] == "balance"
    assert context["kfc_content"]
    holidays = {item["name"]: item for item in context["holidays"]}
    assert holidays["周末休息日"]["days_left"] == 3
    assert holidays["摸鱼纪念日"]["days_left"] == 18
    for item in items:
        assert "今天也要好好吃饭" in renderer._render_template(context, item)
    assert not renderer.images_dir.exists()
