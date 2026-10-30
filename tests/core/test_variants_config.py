"""图片变体配置校验。"""

import pytest
from pydantic import ValidationError

from app.core.config import TemplateRenderConfig


def test_defaults() -> None:
    """变体保持可选启用，并保留原始 JPEG。"""
    config = TemplateRenderConfig()
    assert config.output_formats == ["jpeg"]
    assert config.output_widths == []
    assert config.webp_quality == 85


@pytest.mark.parametrize("formats", [[], ["webp"], ["jpeg", "jpeg"], ["jpeg", "webp", "webp"], ["png"]])
def test_invalid_formats(formats: list[str]) -> None:
    """格式列表必须包含 JPEG，且只能使用已知格式，不得重复。"""
    with pytest.raises(ValidationError):
        TemplateRenderConfig.model_validate({"output_formats": formats})


@pytest.mark.parametrize("widths", [[0], [-1], [60, 60], [1.5], [True], ["60"]])
def test_invalid_widths(widths: list[object]) -> None:
    """宽度以最终像素为单位，只接受不重复的正整数。"""
    with pytest.raises(ValidationError):
        TemplateRenderConfig.model_validate({"output_widths": widths})


@pytest.mark.parametrize("quality", [0, 101, True, 2.5])
def test_invalid_quality(quality: object) -> None:
    """拒绝超出 1–100 整数范围的 WebP 质量。"""
    with pytest.raises(ValidationError):
        TemplateRenderConfig.model_validate({"webp_quality": quality})
