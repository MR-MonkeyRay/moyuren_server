"""Unit tests for atomic JPEG-derived image variants."""

import logging
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image

from app.core.config import TemplateRenderConfig
from app.core.errors import RenderError
from app.services.variants import ensure_variant, generate_variants


@pytest.fixture
def source(tmp_path: Path) -> Path:
    """Create a small source with metadata that variants must discard."""
    path = tmp_path / "moyuren_20261029_060000.jpg"
    image = Image.new("RGB", (120, 80), "red")
    exif = Image.Exif()
    exif[270] = "private description"
    image.save(path, exif=exif)
    image.close()
    return path


def test_expected_files_dimensions_and_metadata(source: Path) -> None:
    """Full WebP and downscaled JPEG/WebP have final pixel widths and no EXIF."""
    config = TemplateRenderConfig(output_formats=["jpeg", "webp"], output_widths=[60])
    variants = generate_variants(source, config, logging.getLogger("test"))
    assert {(v.filename, v.format, v.width) for v in variants} == {
        (source.name, "jpeg", 120), (f"{source.stem}.webp", "webp", 120),
        (f"{source.stem}_w60.jpg", "jpeg", 60), (f"{source.stem}_w60.webp", "webp", 60),
    }
    for variant in variants[1:]:
        with Image.open(source.parent / variant.filename) as image:
            assert image.size == (variant.width, variant.width * 2 // 3)
            assert image.format == variant.format.upper()
            assert not image.getexif()
    assert not list(source.parent.glob("*.tmp"))


def test_skips_equal_and_larger_widths(source: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Avoid upscaling or redundant full-width copies and explain skips in debug logs."""
    with caplog.at_level(logging.DEBUG):
        variants = generate_variants(
            source, TemplateRenderConfig(output_widths=[120, 240]), logging.getLogger("test"),
        )
    assert len(variants) == 1
    assert len(list(source.parent.iterdir())) == 1
    assert "Skipping variant width 120" in caplog.text
    assert "Skipping variant width 240" in caplog.text


def test_one_failure_does_not_stop_other_variants(source: Path, caplog: pytest.LogCaptureFixture) -> None:
    """An atomic replace failure leaves no temporary file and later conversions succeed."""
    import os

    replace = os.replace

    def fail_one(src: Path, target: Path) -> None:
        if target.name == f"{source.stem}.webp":
            raise OSError("disk failure")
        replace(src, target)

    with patch("app.services.variants.os.replace", side_effect=fail_one):
        variants = generate_variants(
            source, TemplateRenderConfig(output_formats=["jpeg", "webp"], output_widths=[60]),
            logging.getLogger("test"),
        )
    assert len(variants) == 3
    assert all((source.parent / variant.filename).is_file() for variant in variants)
    assert "Image variant failed" in caplog.text
    assert not list(source.parent.glob("*.tmp"))


def test_ensure_is_idempotent(source: Path) -> None:
    """Lazy requests reuse an existing successful representation."""
    config = TemplateRenderConfig(output_formats=["jpeg", "webp"])
    first = ensure_variant(source, config, "webp", None)
    with patch("app.services.variants.os.replace", side_effect=AssertionError("unexpected rewrite")):
        assert ensure_variant(source, config, "webp", None) == first


def test_failed_source_uses_application_error(tmp_path: Path) -> None:
    """Source read failures obey the application's error contract."""
    with pytest.raises(RenderError):
        generate_variants(tmp_path / "missing.jpg", TemplateRenderConfig(), logging.getLogger("test"))
