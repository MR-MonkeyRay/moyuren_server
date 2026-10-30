"""服务发布产物遵循启动时 umask。"""

import json
import logging
import os
import stat
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from app.core import fileutil
from app.core.config import NotifyConfig, TemplateRenderConfig, TemplatesConfig, WebhookConfig
from app.models.schemas import DailyDataFile
from app.services.daily_cache import DailyCache
from app.services.holiday import HolidayService
from app.services.notifier import WebhookNotifier
from app.services.renderer import ImageRenderer
from app.services.variants import generate_variants

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX file permissions")


@pytest.fixture(autouse=True)
def output_umask(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    previous = os.umask(0o022)
    try:
        monkeypatch.setattr(fileutil, "_OUTPUT_MODE", 0o666 & ~fileutil._read_umask())
        yield
    finally:
        os.umask(previous)


@pytest.fixture
def renderer(tmp_path: Path, logger: logging.Logger) -> ImageRenderer:
    images = tmp_path / "images"
    images.mkdir()
    return ImageRenderer(TemplatesConfig(), str(images), TemplateRenderConfig(), logger)


def assert_public(path: Path) -> None:
    assert stat.S_IMODE(path.stat().st_mode) == 0o644
    assert not list(path.parent.glob("*.tmp"))


def test_daily_data_json(tmp_path: Path) -> None:
    target = tmp_path / "data.json"
    DailyDataFile.model_validate({
        "date": "2026-10-30", "updated": "2026/10/30 10:00:00",
        "updated_at": 1, "images": {"moyuren": "moyuren.jpg"},
    }).save_atomic(target)
    assert_public(target)


def test_image_variants(tmp_path: Path, logger: logging.Logger) -> None:
    source = tmp_path / "moyuren.jpg"
    with Image.new("RGB", (120, 80), "red") as image:
        image.save(source)
    variants = generate_variants(
        source, TemplateRenderConfig(output_formats=["jpeg", "webp"], output_widths=[60]), logger,
    )
    assert len(variants) == 4
    for variant in variants:
        assert_public(tmp_path / variant.filename)


def test_renderer_jpeg(renderer: ImageRenderer) -> None:
    renderer._write_file_atomic("moyuren.jpg", b"jpeg bytes")
    target = renderer.images_dir / "moyuren.jpg"
    assert target.read_bytes() == b"jpeg bytes"
    assert_public(target)


def test_renderer_resource_body_and_metadata(renderer: ImageRenderer) -> None:
    body = renderer.resource_cache_dir / "font.body"
    meta = renderer.resource_cache_dir / "font.meta"
    renderer._write_cached_resource(
        body, meta, "https://fonts.gstatic.com/font.woff2", "font/woff2", b"font", 1.0,
    )
    assert body.read_bytes() == b"font"
    assert json.loads(meta.read_text())["size"] == 4
    assert_public(body)
    assert_public(meta)


class ExampleDailyCache(DailyCache[dict[str, Any]]):
    async def fetch_fresh(self) -> dict[str, Any] | None:
        return {"value": "fresh"}


def test_daily_source_cache(tmp_path: Path, logger: logging.Logger) -> None:
    cache = ExampleDailyCache("test", tmp_path, logger)
    cache.save_cache({"value": "fresh"})
    assert_public(tmp_path / "test.json")


def test_holiday_year_cache(tmp_path: Path, logger: logging.Logger) -> None:
    service = HolidayService(logger=logger, cache_dir=tmp_path)
    service._save_to_cache(2026, {"year": 2026, "days": []})
    assert_public(tmp_path / "2026.json")


def test_notification_state_has_normal_mode_and_no_secrets(
    tmp_path: Path, logger: logging.Logger,
) -> None:
    notifier = WebhookNotifier(
        config=NotifyConfig(webhooks=[WebhookConfig(
            name="primary", type="generic", url="https://hooks.example/secret-token",
            secret="signing-secret",
        )]),
        cache_dir=tmp_path, base_domain="https://example.test", logger=logger,
    )
    notifier._state = {"date": "2026-10-30", "sent": {"primary": {"moyuren": 123}}}
    notifier._save_state()
    target = tmp_path / "notify_state.json"
    assert json.loads(target.read_text()) == notifier._state
    assert "secret-token" not in target.read_text()
    assert "signing-secret" not in target.read_text()
    assert_public(target)
