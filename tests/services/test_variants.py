"""测试从 JPEG 原子生成图片变体。"""

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
    """创建带元数据的小尺寸原图，变体必须剔除这些元数据。"""
    path = tmp_path / "moyuren_20261029_060000.jpg"
    image = Image.new("RGB", (120, 80), "red")
    exif = Image.Exif()
    exif[270] = "private description"
    image.save(path, exif=exif)
    image.close()
    return path


def test_expected_files_dimensions_and_metadata(source: Path) -> None:
    """原尺寸 WebP 和缩小后的 JPEG/WebP 宽度正确且不含 EXIF。"""
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
    """避免放大或重复生成原宽度副本，并在 debug 日志中说明跳过原因。"""
    with caplog.at_level(logging.DEBUG):
        variants = generate_variants(
            source, TemplateRenderConfig(output_widths=[120, 240]), logging.getLogger("test"),
        )
    assert len(variants) == 1
    assert len(list(source.parent.iterdir())) == 1
    assert "Skipping variant width 120" in caplog.text
    assert "Skipping variant width 240" in caplog.text


def test_one_failure_does_not_stop_other_variants(source: Path, caplog: pytest.LogCaptureFixture) -> None:
    """原子替换失败后不残留临时文件，且后续转换仍能成功。"""
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
    """按需请求复用已成功生成的变体。"""
    config = TemplateRenderConfig(output_formats=["jpeg", "webp"])
    first = ensure_variant(source, config, "webp", None)
    with patch("app.services.variants.os.replace", side_effect=AssertionError("unexpected rewrite")):
        assert ensure_variant(source, config, "webp", None) == first


def test_failed_source_uses_application_error(tmp_path: Path) -> None:
    """原图读取失败时遵循应用的异常约定。"""
    with pytest.raises(RenderError):
        generate_variants(tmp_path / "missing.jpg", TemplateRenderConfig(), logging.getLogger("test"))
