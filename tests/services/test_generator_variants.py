"""Variant integration without browsers, executor threads or external services."""

import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from PIL import Image

from app.core.config import TemplateRenderConfig
from app.models.schemas import DailyDataFile
from app.services.generator import ImageGenerator, _update_data_file


async def inline(function: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Execute synchronous work in the test's current thread."""
    return function(*args, **kwargs)


@asynccontextmanager
async def unlocked(*args: object, **kwargs: object) -> AsyncIterator[None]:
    """Avoid exercising OS lock polling in this integration unit test."""
    yield


@pytest.mark.parametrize("failed", [False, True])
async def test_pipeline_records_variants_and_survives_failure(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, failed: bool,
) -> None:
    """Variant failure leaves the successfully rendered base image usable."""
    images_dir = tmp_path / "images"
    images_dir.mkdir()
    filename = "moyuren_20261029_060000.jpg"
    with Image.new("RGB", (120, 80), "red") as image:
        image.save(images_dir / filename)
    render_config = TemplateRenderConfig(output_formats=["jpeg", "webp"], output_widths=[60])
    templates = SimpleNamespace(items=[SimpleNamespace(name="moyuren")], config=render_config)
    config = SimpleNamespace(paths=SimpleNamespace(cache_dir=str(tmp_path)), get_templates_config=lambda: templates)
    services = SimpleNamespace(
        image_renderer=SimpleNamespace(render=AsyncMock(return_value=filename)),
        data_computer=SimpleNamespace(compute=MagicMock(return_value={"date": {}})),
        cache_cleaner=MagicMock(),
    )
    app = SimpleNamespace(state=SimpleNamespace(config=config, services=services, logger=logging.getLogger("test")))
    now = datetime(2026, 10, 29, 6, tzinfo=timezone.utc)
    with (
        patch("app.services.generator.async_file_lock", unlocked),
        patch("app.services.generator._fetch_all_data_parallel", AsyncMock(return_value={})),
        patch("app.services.generator._schedule_cache_cleanup"),
        patch("app.services.generator.asyncio.to_thread", inline),
        patch("app.services.generator.now_business", return_value=now),
    ):
        if failed:
            with patch("app.services.generator.generate_variants", side_effect=OSError("variant failure")):
                result = await ImageGenerator()._run_generation(app)
        else:
            result = await ImageGenerator()._run_generation(app)
    assert result == {"moyuren": filename}
    stored = DailyDataFile.load(tmp_path / "data" / "2026-10-29.json")
    assert stored.images == result
    assert stored.image_variants is not None
    variants = stored.image_variants["moyuren"]
    if failed:
        assert variants == []
        assert "Image variants failed" in caplog.text
    else:
        assert len(variants) == 4
        assert all((images_dir / variant.filename).is_file() for variant in variants)


async def test_update_replaces_only_current_templates_variants(tmp_path: Path) -> None:
    """A partial render keeps other templates' variants without stale current entries."""
    now = datetime(2026, 10, 29, 6, tzinfo=timezone.utc)
    path = tmp_path / "2026-10-29.json"
    DailyDataFile.model_validate({
        "date": "2026-10-29", "updated": "old", "updated_at": 1,
        "images": {"moyuren": "old.jpg", "other": "other.jpg"},
        "image_variants": {
            "moyuren": [{"filename": "old.webp", "format": "webp", "width": 120}],
            "other": [{"filename": "other.webp", "format": "webp", "width": 120}],
        },
    }).save_atomic(path)
    with patch("app.services.generator.asyncio.to_thread", inline):
        await _update_data_file(str(tmp_path), "new.jpg", {}, {}, None, "moyuren", now, [])
    result = DailyDataFile.load(path)
    assert result.image_variants is not None
    assert result.image_variants["moyuren"] == []
    assert result.image_variants["other"][0].filename == "other.webp"
