"""Notification scheduling integration for image generation."""

import asyncio
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI

from app.core.services import AppServices
from app.services.generator import ImageGenerator


def _build_app(
    generator: ImageGenerator,
    notifier: AsyncMock,
    triggers: list[str],
) -> FastAPI:
    """Build a minimal application around the shared generator."""
    app = FastAPI()
    app.state.config = SimpleNamespace(
        notify=SimpleNamespace(triggers=triggers),
        get_templates_config=lambda: SimpleNamespace(
            items=[SimpleNamespace(name="moyuren")]
        ),
    )
    app.state.logger = logging.getLogger("test.generator.notify")
    app.state.services = cast(
        AppServices,
        SimpleNamespace(generator=generator, notifier=notifier),
    )
    return app


def _generation_data() -> dict[str, Any]:
    return {
        "date": "2026-10-29",
        "weekday": "星期四",
        "lunar_date": "九月二十",
        "updated_at": 1793268000000,
        "is_stale": False,
    }


@pytest.mark.asyncio
async def test_notification_is_scheduled_without_delaying_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Webhook delivery runs in a retained task after generation returns."""
    generator = ImageGenerator()
    notify_started = asyncio.Event()
    release_notify = asyncio.Event()

    async def notify(**kwargs: object) -> None:
        notify_started.set()
        await release_notify.wait()

    notifier = AsyncMock()
    notifier.notify.side_effect = notify
    app = _build_app(generator, notifier, ["scheduled"])

    async def run_generation(*args: object) -> dict[str, str]:
        generator._notification_images = {"moyuren": "moyuren.jpg"}
        generator._notification_data = _generation_data()
        return {"moyuren": "moyuren.jpg"}

    monkeypatch.setattr(generator, "_run_generation", run_generation)

    result = await generator.generate(app, trigger="scheduled")
    await asyncio.wait_for(notify_started.wait(), timeout=1)

    assert result == {"moyuren": "moyuren.jpg"}
    assert not release_notify.is_set()
    notifier.notify.assert_awaited_once_with(
        images={"moyuren": "moyuren.jpg"},
        data=_generation_data(),
        trigger="scheduled",
    )
    release_notify.set()
    await generator.shutdown()


@pytest.mark.asyncio
async def test_notification_respects_trigger_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A generation outside configured triggers does not schedule delivery."""
    generator = ImageGenerator()
    notifier = AsyncMock()
    app = _build_app(generator, notifier, ["scheduled"])

    async def run_generation(*args: object) -> dict[str, str]:
        generator._notification_images = {"moyuren": "moyuren.jpg"}
        generator._notification_data = _generation_data()
        return {"moyuren": "moyuren.jpg"}

    monkeypatch.setattr(generator, "_run_generation", run_generation)

    assert await generator.generate(app, trigger="manual") == {
        "moyuren": "moyuren.jpg"
    }
    await asyncio.sleep(0)

    notifier.notify.assert_not_awaited()


@pytest.mark.asyncio
async def test_single_flight_joiner_does_not_duplicate_notification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only the shared generation task schedules webhook delivery."""
    generator = ImageGenerator()
    notifier = AsyncMock()
    app = _build_app(generator, notifier, ["manual"])
    started = asyncio.Event()
    release = asyncio.Event()

    async def run_generation(*args: object) -> dict[str, str]:
        started.set()
        await release.wait()
        generator._notification_images = {"moyuren": "moyuren.jpg"}
        generator._notification_data = _generation_data()
        return {"moyuren": "moyuren.jpg"}

    monkeypatch.setattr(generator, "_run_generation", run_generation)

    creator = asyncio.create_task(generator.generate(app))
    await started.wait()
    joiner = asyncio.create_task(generator.generate(app))
    release.set()
    assert await asyncio.gather(creator, joiner) == [
        {"moyuren": "moyuren.jpg"},
        {"moyuren": "moyuren.jpg"},
    ]
    await asyncio.sleep(0)

    notifier.notify.assert_awaited_once()


@pytest.mark.asyncio
async def test_notification_failure_never_propagates(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A notifier exception is contained in the retained background task."""
    generator = ImageGenerator()
    notifier = AsyncMock()
    notifier.notify.side_effect = RuntimeError("delivery unavailable")
    app = _build_app(generator, notifier, ["manual"])

    async def run_generation(*args: object) -> dict[str, str]:
        generator._notification_images = {"moyuren": "moyuren.jpg"}
        generator._notification_data = _generation_data()
        return {"moyuren": "moyuren.jpg"}

    monkeypatch.setattr(generator, "_run_generation", run_generation)

    assert await generator.generate(app) == {"moyuren": "moyuren.jpg"}
    await asyncio.sleep(0)

    assert "Webhook notification task failed: RuntimeError" in caplog.text


@pytest.mark.asyncio
async def test_shutdown_cancels_retained_notification_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Generator shutdown drains notification work before shared clients close."""
    generator = ImageGenerator()
    notify_started = asyncio.Event()
    notify_cancelled = asyncio.Event()

    async def notify(**kwargs: object) -> None:
        notify_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            notify_cancelled.set()

    notifier = AsyncMock()
    notifier.notify.side_effect = notify
    app = _build_app(generator, notifier, ["manual"])

    async def run_generation(*args: object) -> dict[str, str]:
        generator._notification_images = {"moyuren": "moyuren.jpg"}
        generator._notification_data = _generation_data()
        return {"moyuren": "moyuren.jpg"}

    monkeypatch.setattr(generator, "_run_generation", run_generation)

    await generator.generate(app)
    await notify_started.wait()
    await generator.shutdown()

    assert notify_cancelled.is_set()
    assert not generator._notification_tasks



@pytest.mark.asyncio
async def test_actual_pipeline_passes_persisted_metadata_to_notifier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rendered filenames and the exact saved business snapshot reach delivery."""
    import json
    from datetime import datetime
    from zoneinfo import ZoneInfo

    import app.services.generator as generator_module

    async def inline(function: Any, *args: Any, **kwargs: Any) -> Any:
        return function(*args, **kwargs)

    generator = ImageGenerator()
    notifier = AsyncMock()
    app = _build_app(generator, notifier, ["startup"])
    app.state.config.paths = SimpleNamespace(cache_dir=str(tmp_path))
    app.state.services.data_computer = SimpleNamespace(compute=lambda *args, **kwargs: {
        "date": {"week_cn": "星期四", "lunar_date": "九月二十"},
        "data_freshness": {"is_stale": True},
    })
    app.state.services.image_renderer = SimpleNamespace(
        render=AsyncMock(return_value="moyuren.jpg")
    )
    app.state.services.cache_cleaner = object()
    monkeypatch.setattr(generator_module, "_fetch_all_data_parallel", AsyncMock(return_value={}))
    monkeypatch.setattr(generator_module, "generate_variants", lambda *args: [])
    monkeypatch.setattr(generator_module, "_schedule_cache_cleanup", lambda *args: None)
    monkeypatch.setattr(generator_module.asyncio, "to_thread", inline)
    monkeypatch.setattr(generator_module, "now_business", lambda: datetime(
        2026, 10, 29, 6, 0, tzinfo=ZoneInfo("Asia/Shanghai"),
    ))

    result = await generator.generate(app, trigger="startup")
    await asyncio.sleep(0)
    saved = json.loads((tmp_path / "data" / "2026-10-29.json").read_text())
    notifier.notify.assert_awaited_once()
    sent = notifier.notify.await_args.kwargs
    assert sent["images"] == {"moyuren": "moyuren.jpg"}
    assert sent["trigger"] == "startup"
    for field in ("date", "updated_at", "weekday", "lunar_date", "is_stale"):
        assert sent["data"][field] == saved[field]
    assert result == {"moyuren": "moyuren.jpg"}
    assert saved["is_stale"] is True
    await generator.shutdown()
