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
import app.core.bootstrap as bootstrap_module
from app.core.services import AppServices


@pytest.fixture
def startup_app(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mock_services: AppServices
) -> FastAPI:
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
    monkeypatch.setattr(mock_services.holiday_service, "get", AsyncMock())
    monkeypatch.setattr(mock_services.generator, "generate", AsyncMock())
    monkeypatch.setattr(mock_services.generator, "shutdown", AsyncMock())
    app.state.services = mock_services
    app.state.http_client = SimpleNamespace(aclose=AsyncMock())
    app.state.scheduler = SimpleNamespace(
        add_hourly_job=MagicMock(), start=MagicMock(), shutdown=MagicMock(),
    )
    monkeypatch.setattr(main_module, "load_config", lambda: config)
    monkeypatch.setattr(main_module, "setup_logging", lambda *args, **kwargs: app.state.logger)
    monkeypatch.setattr(main_module, "init_timezones", lambda **kwargs: None)
    monkeypatch.setattr(bootstrap_module, "today_business", lambda: date(2026, 2, 4))
    monkeypatch.setattr(main_module, "browser_manager", app.state.browser_manager)
    monkeypatch.setattr(main_module.httpx, "AsyncClient", lambda **kwargs: app.state.http_client)
    monkeypatch.setattr(main_module, "build_services", lambda *args: app.state.services)
    monkeypatch.setattr(main_module, "TaskScheduler", lambda *args, **kwargs: app.state.scheduler)
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
    startup_app.state.services.holiday_service.get.side_effect = warmup
    async with asyncio.timeout(1), main_module.lifespan(startup_app):
        startup_app.state.scheduler.start.assert_called_once()
        assert startup_app.state.startup_state == "starting"
        assert startup_app.state.startup_started_at is not None
        assert startup_app.state.startup_completed_at is None
        await asyncio.wait_for(warmup_started.wait(), timeout=1)
        assert not startup_app.state.startup_task.done()
        startup_app.state.services.generator.generate.assert_not_awaited()

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

    async def generate(
        app: FastAPI, template_name: str | None = None, trigger: str = "manual"
    ) -> None:
        assert allow_warmup.is_set()
        assert template_name is None
        assert trigger == "startup"
        generation_started.set()
        await allow_generation.wait()

    startup_app.state.services.holiday_service.get.side_effect = warmup
    startup_app.state.services.generator.generate.side_effect = generate
    async with asyncio.timeout(1), main_module.lifespan(startup_app):
        await asyncio.wait_for(warmup_started.wait(), timeout=1)
        startup_app.state.services.generator.generate.assert_not_awaited()
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
    startup_app.state.services.holiday_service.get.side_effect = RuntimeError("source unavailable")
    async with asyncio.timeout(1), main_module.lifespan(startup_app):
        await asyncio.wait_for(startup_app.state.startup_task, timeout=1)
        assert startup_app.state.startup_state == "ready"
        assert "Cache warmup [holiday] failed" in caplog.text
        startup_app.state.services.generator.generate.assert_awaited_once_with(
            startup_app, None, "startup"
        )


@pytest.mark.asyncio
async def test_unexpected_startup_failure_is_logged(
    startup_app: FastAPI, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Unexpected task errors mark startup failed without escaping lifespan."""
    monkeypatch.setattr(
        main_module, "ensure_today_data", AsyncMock(side_effect=RuntimeError("unexpected failure"))
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

    async def generate(
        app: FastAPI, template_name: str | None = None, trigger: str = "manual"
    ) -> None:
        assert template_name is None
        assert trigger == "startup"
        assert not data_file.exists()

    startup_app.state.services.generator.generate.side_effect = generate
    async with asyncio.timeout(1), main_module.lifespan(startup_app):
        await asyncio.wait_for(startup_app.state.startup_task, timeout=1)
        startup_app.state.services.generator.generate.assert_awaited_once_with(
            startup_app, None, "startup"
        )
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
        json.dumps(
            {
                "date": cached_date,
                "updated": "2026/02/04 00:00:00",
                "updated_at": 1,
                "images": {"moyuren": "x.jpg"},
            }
        ),
        encoding="utf-8",
    )
    async with asyncio.timeout(1), main_module.lifespan(startup_app):
        await asyncio.wait_for(startup_app.state.startup_task, timeout=1)
        assert data_file.exists()
        if cached_date == "2026-02-04":
            startup_app.state.services.generator.generate.assert_not_awaited()
        else:
            startup_app.state.services.generator.generate.assert_awaited_once_with(
                startup_app, None, "startup"
            )
        assert startup_app.state.startup_state == "ready"


@pytest.mark.asyncio
async def test_initial_generation_failure_still_finishes_ready(
    startup_app: FastAPI, caplog: pytest.LogCaptureFixture
) -> None:
    """Expected generation errors retain on-demand fallback without failing startup."""
    startup_app.state.services.generator.generate.side_effect = RuntimeError(
        "render unavailable"
    )
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
        json.dumps(
            {
                "date": "2026-02-03",
                "updated": "2026/02/03 00:00:00",
                "updated_at": 1,
                "images": {"moyuren": "x.jpg"},
            }
        ),
        encoding="utf-8",
    )
    startup_app.state.services.generator.generate.side_effect = RuntimeError(
        "render unavailable"
    )
    async with asyncio.timeout(1), main_module.lifespan(startup_app):
        await asyncio.wait_for(startup_app.state.startup_task, timeout=1)
        assert startup_app.state.startup_state == "ready"
        assert data_file.exists()
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
            json.dumps(
                {
                    "date": "2026-02-03",
                    "updated": "2026/02/03 00:00:00",
                    "updated_at": 1,
                    "images": {"moyuren": "x.jpg"},
                }
            ),
            encoding="utf-8",
        )

    async def generate(
        app: FastAPI, template_name: str | None = None, trigger: str = "manual"
    ) -> None:
        assert template_name is None
        assert trigger == "startup"
        generation_started.set()
        await allow_generation.wait()

    def stop_scheduler() -> None:
        assert startup_app.state.startup_task.cancelled()

    startup_app.state.services.generator.generate.side_effect = generate
    startup_app.state.scheduler.shutdown.side_effect = stop_scheduler
    async with asyncio.timeout(1), main_module.lifespan(startup_app):
        await asyncio.wait_for(generation_started.wait(), timeout=1)
        assert not startup_app.state.startup_task.done()

    assert startup_app.state.startup_task.cancelled()
    startup_app.state.scheduler.shutdown.assert_called_once()
