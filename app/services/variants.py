"""从已渲染的 JPEG 生成图片变体，无需再启动浏览器。"""

import logging
import os
import tempfile
from pathlib import Path
from typing import Literal

from PIL import Image

from app.core.config import TemplateRenderConfig
from app.core.errors import ErrorCode, RenderError
from app.core.fileutil import set_output_permissions
from app.models.schemas import ImageVariant


def image_width(source: Path) -> int:
    """读取图片最终宽度，与浏览器 device scale 无关。"""
    try:
        with Image.open(source) as image:
            return image.width
    except Exception as exc:
        raise RenderError("Failed to read source JPEG", ErrorCode.RENDER_SAVE_FAILED) from exc


def ensure_variant(
    source: Path,
    config: TemplateRenderConfig,
    format: Literal["jpeg", "webp"],
    width: int | None,
) -> ImageVariant:
    """原子写入单个缺失的图片变体；并发写入安全。"""
    temporary: Path | None = None
    try:
        with Image.open(source) as original:
            final_width = original.width if width is None else min(width, original.width)
            suffix = "" if final_width == original.width else f"_w{final_width}"
            extension = "jpg" if format == "jpeg" else "webp"
            target = source.with_name(f"{source.stem}{suffix}.{extension}")
            variant = ImageVariant(filename=target.name, format=format, width=final_width)
            if target.is_file():
                return variant

            # 新建 RGB 图像会丢弃 EXIF、ICC profile 等原图元数据。
            image = original.convert("RGB")
            try:
                if final_width != original.width:
                    resized = image.resize(
                        (final_width, max(1, round(original.height * final_width / original.width))),
                        Image.Resampling.LANCZOS,
                    )
                    image.close()
                    image = resized
                image.info.clear()
                with tempfile.NamedTemporaryFile(
                    dir=source.parent, prefix=".variant_", suffix=".tmp", delete=False,
                ) as handle:
                    temporary = Path(handle.name)
                image.save(
                    temporary,
                    format="JPEG" if format == "jpeg" else "WEBP",
                    quality=config.jpeg_quality if format == "jpeg" else config.webp_quality,
                )
                set_output_permissions(temporary)
                os.replace(temporary, target)
            finally:
                image.close()
            return variant
    except Exception as exc:
        raise RenderError(
            message=f"Failed to create {format} image variant at width {width}: {exc}",
            code=ErrorCode.RENDER_SAVE_FAILED,
        ) from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def generate_variants(
    source: Path, config: TemplateRenderConfig, logger: logging.Logger,
) -> list[ImageVariant]:
    """返回原始 JPEG 与成功的变体；单个变体失败不影响其余。"""
    original_width = image_width(source)
    variants = [ImageVariant(filename=source.name, format="jpeg", width=original_width)]
    widths: list[int | None] = [None]
    for configured_width in config.output_widths:
        if configured_width >= original_width:
            logger.debug("Skipping variant width %d >= source width %d", configured_width, original_width)
        else:
            widths.append(configured_width)
    for width in widths:
        for format in config.output_formats:
            if format == "jpeg" and width is None:
                continue
            try:
                variants.append(ensure_variant(source, config, format, width))
            except Exception as exc:
                logger.warning("Image variant failed for %s (%s, width=%s): %s", source.name, format, width, exc)
    return variants
