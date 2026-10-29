"""Tests for non-blocking startup and initial cache generation."""

import asyncio
import json
import logging
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI

import app.main as main_module


@pytest.fixture
def startup_app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> FastAPI:
    """Create an app with local configuration and mocked startup dependencies."""
    config = SimpleNamespace(
        paths=SimpleNamespace(cache_dir=str(tmp_path / "cache")),
        logging=SimpleNamespace(file=str(tmp_path / "logs" / "app.log")),
        timezone=SimpleNamespace(business="Asia/Shanghai", display="local"),
        network=SimpleNamespace(proxy_url=None, ghproxy_urls=[]),
        instances=SimpleNamespace(),
        templates=SimpleNamespace(config=SimpleNamespace()),
        cache=SimpleNamespace(retain_days=30, max_images_per_template_per_day=3),
        scheduler=SimpleNamespace(mode="hourly", minute_of_hour=0),
        get_source=lambda source_type: None,
        get_templates_config=lambda: SimpleNamespace(items=[]),
    )
    app = FastAPI()
    app.state.config = config
    app.state.logger = logging.getLogger("test.main.startup")
    app.state.browser_manager = SimpleNamespace(
        configure=MagicMock(), warmup=AsyncMock(), shutdown=AsyncMock(),
    )
    app.state.holiday_service = SimpleNamespace(get=AsyncMock())
    app.state.http_client = SimpleNamespace(aclose=AsyncMock())
    app.state.scheduler = SimpleNamespace(
        add_hourly_job=MagicMock(), start=MagicMock(), shutdown=MagicMock(),
    )
    monkeypatch.setattr(main_module, "load_config", lambda: config)
    monkeypatch.setattr(main_module, "setup_logging", lambda *args, **kwargs: app.state.logger)
    monkeypatch.setattr(main_module, "init_timezones", lambda **kwargs: None)
    monkeypatch.setattr(main_module, "today_business", lambda: date(2026, 2, 4))
    monkeypatch.setattr(main_module, "browser_manager", app.state.browser_manager)
    monkeypatch.setattr(main_module.httpx, "AsyncClient", lambda **kwargs: app.state.http_client)
    monkeypatch.setattr(main_module, "CachedHolidayService", lambda **kwargs: app.state.holiday_service)
    monkeypatch.setattr(main_module, "ImageRenderer", lambda **kwargs: MagicMock())
    monkeypatch.setattr(main_module, "CacheCleaner", lambda **kwargs: MagicMock())
    monkeypatch.setattr(main_module, "TaskScheduler", lambda *args, **kwargs: app.state.scheduler)
    app.state.generate_image = AsyncMock()
    monkeypatch.setattr(main_module, "generate_and_save_image", app.state.generate_image)
    return app


@pytest.mark.asyncio
async def test_lifespan_yields_before_warmup_and_cancels_startup(startup_app: FastAPI) -> None:
    """Startup yields while cache warmup is pending and shutdown cancels it."""
    warmup_started = asyncio.Event()
    allow_warmup = asyncio.Event()

    async def warmup() -> None:
        warmup_started.set()
        await allow_warmup.wait()

    def stop_scheduler() -> None:
        assert startup_app.state.startup_task.cancelled()

    startup_app.state.scheduler.shutdown.side_effect = stop_scheduler
    startup_app.state.holiday_service.get.side_effect = warmup
    async with asyncio.timeout(1), main_module.lifespan(startup_app):
        startup_app.state.scheduler.start.assert_called_once()
        assert startup_app.state.startup_state == "starting"
        assert startup_app.state.startup_started_at is not None
        assert startup_app.state.startup_completed_at is None
        await asyncio.wait_for(warmup_started.wait(), timeout=1)
        assert not startup_app.state.startup_task.done()
        startup_app.state.generate_image.assert_not_awaited()

    assert startup_app.state.startup_task.cancelled()
    startup_app.state.http_client.aclose.assert_awaited_once()
    startup_app.state.browser_manager.shutdown.assert_awaited_once()
    startup_app.state.scheduler.shutdown.assert_called_once()


@pytest.mark.asyncio
async def test_startup_warms_cache_before_generating_missing_data(startup_app: FastAPI) -> None:
    """Initial generation starts only after daily cache warmup completes."""
    warmup_started = asyncio.Event()
    allow_warmup = asyncio.Event()
    generation_started = asyncio.Event()
    allow_generation = asyncio.Event()

    async def warmup() -> None:
        warmup_started.set()
        await allow_warmup.wait()

    async def generate(app: FastAPI) -> None:
        assert allow_warmup.is_set()
        generation_started.set()
        await allow_generation.wait()

    startup_app.state.holiday_service.get.side_effect = warmup
    startup_app.state.generate_image.side_effect = generate
    async with asyncio.timeout(1), main_module.lifespan(startup_app):
        await asyncio.wait_for(warmup_started.wait(), timeout=1)
        startup_app.state.generate_image.assert_not_awaited()
        allow_warmup.set()
        await asyncio.wait_for(generation_started.wait(), timeout=1)
        assert startup_app.state.startup_state == "starting"
        allow_generation.set()
        await asyncio.wait_for(startup_app.state.startup_task, timeout=1)
        assert startup_app.state.startup_state == "ready"
        assert startup_app.state.startup_completed_at >= startup_app.state.startup_started_at


@pytest.mark.asyncio
async def test_startup_source_failure_still_finishes_ready(
    startup_app: FastAPI, caplog: pytest.LogCaptureFixture
) -> None:
    """A source warmup error is logged without failing the startup task."""
    startup_app.state.holiday_service.get.side_effect = RuntimeError("source unavailable")
    async with asyncio.timeout(1), main_module.lifespan(startup_app):
        await asyncio.wait_for(startup_app.state.startup_task, timeout=1)
        assert startup_app.state.startup_state == "ready"
        assert "Cache warmup [holiday] failed" in caplog.text
        startup_app.state.generate_image.assert_awaited_once_with(startup_app)


@pytest.mark.asyncio
async def test_unexpected_startup_failure_is_logged(
    startup_app: FastAPI, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Unexpected task errors mark startup failed without escaping lifespan."""
    monkeypatch.setattr(
        main_module, "_ensure_today_data", AsyncMock(side_effect=RuntimeError("unexpected failure"))
    )
    async with asyncio.timeout(1), main_module.lifespan(startup_app):
        await asyncio.wait_for(startup_app.state.startup_task, timeout=1)
        assert startup_app.state.startup_state == "failed"
        assert startup_app.state.startup_completed_at is not None
        assert "Startup task failed" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid_data",
    [
        "invalid json", "[]", '{"images": {}}', "directory",
        '{"images": {"moyuren": "x.jpg"}}',
        '{"images": {"moyuren": "x.jpg"}, "updated_at": "invalid"}',
        '{"images": {"moyuren": "x.jpg"}, "updated_at": -1}',
    ],
)
async def test_initial_generation_removes_invalid_data(
    startup_app: FastAPI, invalid_data: str
) -> None:
    """Invalid cache files and directories are removed before regeneration."""
    data_dir = Path(startup_app.state.config.paths.cache_dir) / "data"
    data_dir.mkdir(parents=True)
    data_file = data_dir / "2026-02-04.json"
    if invalid_data == "directory":
        data_file.mkdir()
        (data_file / "invalid").write_text("invalid", encoding="utf-8")
    else:
        data_file.write_text(invalid_data, encoding="utf-8")

    async def generate(app: FastAPI) -> None:
        assert not data_file.exists()

    startup_app.state.generate_image.side_effect = generate
    async with asyncio.timeout(1), main_module.lifespan(startup_app):
        await asyncio.wait_for(startup_app.state.startup_task, timeout=1)
        startup_app.state.generate_image.assert_awaited_once_with(startup_app)
        assert not data_file.exists()
        assert startup_app.state.startup_state == "ready"


@pytest.mark.asyncio
@pytest.mark.parametrize("cached_date", ["2026-02-04", "2026-02-03"])
async def test_startup_preserves_valid_cache(startup_app: FastAPI, cached_date: str) -> None:
    """Valid caches are preserved and expired data is refreshed in the startup task."""
    data_dir = Path(startup_app.state.config.paths.cache_dir) / "data"
    data_dir.mkdir(parents=True)
    data_file = data_dir / "2026-02-04.json"
    data_file.write_text(
        json.dumps({"date": cached_date, "updated_at": 1, "images": {"moyuren": "x.jpg"}}),
        encoding="utf-8",
    )
    async with asyncio.timeout(1), main_module.lifespan(startup_app):
        await asyncio.wait_for(startup_app.state.startup_task, timeout=1)
        assert data_file.exists()
        assert not startup_app.state.is_refreshing
        if cached_date == "2026-02-04":
            startup_app.state.generate_image.assert_not_awaited()
        else:
            startup_app.state.generate_image.assert_awaited_once_with(startup_app)
        assert startup_app.state.startup_state == "ready"


@pytest.mark.asyncio
async def test_initial_generation_failure_still_finishes_ready(
    startup_app: FastAPI, caplog: pytest.LogCaptureFixture
) -> None:
    """Expected generation errors retain on-demand fallback without failing startup."""
    startup_app.state.generate_image.side_effect = RuntimeError("render unavailable")
    async with asyncio.timeout(1), main_module.lifespan(startup_app):
        await asyncio.wait_for(startup_app.state.startup_task, timeout=1)
        assert startup_app.state.startup_state == "ready"
        assert "Initial image generation failed" in caplog.text


@pytest.mark.asyncio
async def test_expired_cache_refresh_failure_preserves_stale_data(
    startup_app: FastAPI, caplog: pytest.LogCaptureFixture
) -> None:
    """A failed expired-cache refresh preserves stale data and clears single-flight state."""
    data_dir = Path(startup_app.state.config.paths.cache_dir) / "data"
    data_dir.mkdir(parents=True)
    data_file = data_dir / "2026-02-04.json"
    data_file.write_text(
        json.dumps({"date": "2026-02-03", "updated_at": 1, "images": {"moyuren": "x.jpg"}}),
        encoding="utf-8",
    )
    startup_app.state.generate_image.side_effect = RuntimeError("render unavailable")
    async with asyncio.timeout(1), main_module.lifespan(startup_app):
        await asyncio.wait_for(startup_app.state.startup_task, timeout=1)
        assert startup_app.state.startup_state == "ready"
        assert data_file.exists()
        assert not startup_app.state.is_refreshing
        assert "Background refresh failed" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("expired_cache", [False, True])
async def test_shutdown_cancels_initial_generation(
    startup_app: FastAPI, expired_cache: bool
) -> None:
    """Shutdown cancels either generation branch before scheduler dependencies stop."""
    generation_started = asyncio.Event()
    allow_generation = asyncio.Event()
    if expired_cache:
        data_dir = Path(startup_app.state.config.paths.cache_dir) / "data"
        data_dir.mkdir(parents=True)
        (data_dir / "2026-02-04.json").write_text(
            json.dumps({"date": "2026-02-03", "updated_at": 1, "images": {"moyuren": "x.jpg"}}),
            encoding="utf-8",
        )

    async def generate(app: FastAPI) -> None:
        generation_started.set()
        await allow_generation.wait()

    def stop_scheduler() -> None:
        assert startup_app.state.startup_task.cancelled()
        assert not startup_app.state.is_refreshing

    startup_app.state.generate_image.side_effect = generate
    startup_app.state.scheduler.shutdown.side_effect = stop_scheduler
    async with asyncio.timeout(1), main_module.lifespan(startup_app):
        await asyncio.wait_for(generation_started.wait(), timeout=1)
        assert not startup_app.state.startup_task.done()

    assert startup_app.state.startup_task.cancelled()
    startup_app.state.scheduler.shutdown.assert_called_once()
