"""Validation of image variant configuration."""

import pytest
from pydantic import ValidationError

from app.core.config import TemplateRenderConfig


def test_defaults() -> None:
    """Variants remain opt-in, retaining the original JPEG."""
    config = TemplateRenderConfig()
    assert config.output_formats == ["jpeg"]
    assert config.output_widths == []
    assert config.webp_quality == 85


@pytest.mark.parametrize("formats", [[], ["webp"], ["jpeg", "jpeg"], ["jpeg", "webp", "webp"], ["png"]])
def test_invalid_formats(formats: list[str]) -> None:
    """Require JPEG, known formats and uniqueness."""
    with pytest.raises(ValidationError):
        TemplateRenderConfig.model_validate({"output_formats": formats})


@pytest.mark.parametrize("widths", [[0], [-1], [60, 60], [1.5], [True], ["60"]])
def test_invalid_widths(widths: list[object]) -> None:
    """Only unique positive integer widths represent final pixels."""
    with pytest.raises(ValidationError):
        TemplateRenderConfig.model_validate({"output_widths": widths})


@pytest.mark.parametrize("quality", [0, 101, True, 2.5])
def test_invalid_quality(quality: object) -> None:
    """Reject WebP quality outside the integer 1–100 range."""
    with pytest.raises(ValidationError):
        TemplateRenderConfig.model_validate({"webp_quality": quality})
