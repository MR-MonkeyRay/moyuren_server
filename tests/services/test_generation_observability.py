"""Generation metrics and freshness propagation without browser or worker threads."""

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI

import app.services.generator as generator_module
from app.core.errors import RenderError, StorageError
from app.core.metrics import registry
from app.core.services import AppServices
from app.models.schemas import DailyDataFile
from app.services.compute import DataComputer
from app.services.generator import ImageGenerator, _fetch_all_data_parallel


@pytest.mark.asyncio
async def test_fetch_collects_stale_sources_without_mutating_cache(
    mock_services: AppServices,
) -> None:
    """Capture each source fallback and stock staleness in the generation context."""
    cached_news = {"news": {"data": {"date": "2026-03-11", "news": ["headline"]}}}
    mock_services.data_fetcher = cast(Any, SimpleNamespace(
        get=AsyncMock(return_value=cached_news), namespace="news", last_outcome="stale",
    ))
    mock_services.holiday_service = None
    mock_services.gold_price_service = cast(Any, SimpleNamespace(
        get=AsyncMock(return_value={}), namespace="gold_price", last_outcome="stale",
    ))
    mock_services.fun_content_service = cast(Any, SimpleNamespace(
        get=AsyncMock(return_value={}), namespace="fun_content", last_outcome="cache",
    ))
    mock_services.kfc_service = cast(Any, SimpleNamespace(
        get=AsyncMock(return_value=None), namespace="kfc", last_outcome="stale",
    ))
    mock_services.stock_index_service = cast(Any, SimpleNamespace(
        fetch_indices=AsyncMock(return_value={"is_stale": True}),
    ))
    app = FastAPI()
    app.state.services = mock_services

    raw_data = await _fetch_all_data_parallel(app, logging.getLogger("test.generation"))

    assert raw_data["stale_sources"] == ["gold_price", "news", "stock_index"]
    assert set(cached_news) == {"news"}


@pytest.mark.asyncio
async def test_fetch_ignores_failed_stock_placeholder(mock_services: AppServices) -> None:
    """A failed stock placeholder is shown as missing data, not as a cache fallback."""
    mock_services.data_fetcher = None
    mock_services.holiday_service = None
    mock_services.gold_price_service = None
    mock_services.fun_content_service = None
    mock_services.kfc_service = None
    mock_services.daily_english_service = None
    mock_services.stock_index_service = cast(Any, SimpleNamespace(
        fetch_indices=AsyncMock(return_value={"is_stale": True, "is_data_missing": True}),
    ))
    app = FastAPI()
    app.state.services = mock_services

    raw_data = await _fetch_all_data_parallel(app, logging.getLogger("test.generation"))

    assert raw_data["stale_sources"] == []


@pytest.mark.parametrize(
    ("failed_templates", "expected_result"),
    [([], "success"), (["cute"], "partial"), (["moyuren", "cute"], "failure")],
)
@pytest.mark.asyncio
async def test_generation_observes_render_attempts_and_persists_freshness(
    failed_templates: list[str],
    expected_result: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mock_services: AppServices,
) -> None:
    """Measure whole runs and failed render attempts while saving the actual context."""
    snapshot = datetime(2026, 3, 12, 10, tzinfo=ZoneInfo("Asia/Shanghai"))
    templates = SimpleNamespace(items=[
        SimpleNamespace(name="moyuren"), SimpleNamespace(name="cute"),
    ])
    app = FastAPI()
    app.state.config = SimpleNamespace(
        paths=SimpleNamespace(cache_dir=str(tmp_path)),
        get_templates_config=lambda: templates,
    )
    app.state.logger = logging.getLogger("test.generation")
    app.state.services = mock_services
    mock_services.data_computer = DataComputer()

    async def inline_thread(function: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        return function(*args, **kwargs)

    async def render(context: dict[str, Any], template_name: str, now: datetime) -> str:
        assert now is snapshot
        assert context["data_freshness"] == {
            "is_stale": True, "news_is_stale": True, "source_date": "2026-03-11",
            "stale_sources": ["gold_price"],
        }
        if template_name in failed_templates:
            raise RenderError("render failed")
        return f"{template_name}.jpg"

    mock_services.image_renderer.render = AsyncMock(side_effect=render)
    monkeypatch.setattr(generator_module.asyncio, "to_thread", inline_thread)
    monkeypatch.setattr(generator_module, "now_business", lambda: snapshot)
    monkeypatch.setattr(generator_module, "_schedule_cache_cleanup", MagicMock())
    monkeypatch.setattr(generator_module, "_fetch_all_data_parallel", AsyncMock(return_value={
        "news": {"data": {"date": "2026-03-11", "news": ["headline"]}},
        "stale_sources": ["gold_price"],
    }))
    generation_before = {
        result: registry.get_sample_value("moyuren_generation_duration_seconds_count", {"result": result}) or 0
        for result in ("success", "partial", "failure")
    }
    render_before = {
        name: registry.get_sample_value("moyuren_render_duration_seconds_count", {"template": name}) or 0
        for name in ("moyuren", "cute")
    }

    if expected_result == "failure":
        with pytest.raises(StorageError, match="All templates failed"):
            await mock_services.generator.generate(app)
    else:
        result = await mock_services.generator.generate(app)
        assert set(result) == {"moyuren", "cute"} - set(failed_templates)
        stored = DailyDataFile.load(tmp_path / "data" / "2026-03-12.json")
        assert stored.is_stale is True
        assert stored.source_date == "2026-03-11"
        assert stored.stale_sources == ["gold_price"]
        assert set(stored.images) == set(result)

    for outcome, before in generation_before.items():
        after = registry.get_sample_value("moyuren_generation_duration_seconds_count", {"result": outcome}) or 0
        assert after - before == int(outcome == expected_result)
    for name, before in render_before.items():
        after = registry.get_sample_value("moyuren_render_duration_seconds_count", {"template": name}) or 0
        assert after - before == 1


@pytest.mark.asyncio
async def test_shared_generation_counts_one_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """Concurrent waiters share a single recorded generation task."""
    generator = ImageGenerator()
    app = FastAPI()
    templates = SimpleNamespace(items=[SimpleNamespace(name="moyuren")])
    app.state.config = SimpleNamespace(get_templates_config=lambda: templates)
    started = asyncio.Event()
    release = asyncio.Event()

    async def run(*args: Any) -> dict[str, str]:
        started.set()
        await release.wait()
        return {"moyuren": "moyuren.jpg"}

    monkeypatch.setattr(generator, "_run_generation", run)
    before = registry.get_sample_value("moyuren_generation_duration_seconds_count", {"result": "success"}) or 0
    first = asyncio.create_task(generator.generate(app))
    await started.wait()
    second = asyncio.create_task(generator.generate(app, "moyuren"))
    await asyncio.sleep(0)
    release.set()
    assert await first == await second == {"moyuren": "moyuren.jpg"}
    after = registry.get_sample_value("moyuren_generation_duration_seconds_count", {"result": "success"}) or 0
    assert after - before == 1
