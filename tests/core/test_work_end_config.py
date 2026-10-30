"""Strict validation of the business-time work-end configuration."""

import pytest
from pydantic import ValidationError

from app.core.config import TemplateRenderConfig


def test_work_end_time_defaults_to_eighteen() -> None:
    """The default lives in configuration and remains compatible with old YAML."""
    assert TemplateRenderConfig().work_end_time == "18:00"


@pytest.mark.parametrize("value", ["00:00", "09:05", "18:00", "23:59"])
def test_work_end_time_accepts_valid_hh_mm(value: str) -> None:
    """Accept only zero-padded business clock times."""
    assert TemplateRenderConfig(work_end_time=value).work_end_time == value


@pytest.mark.parametrize("value", ["24:00", "18:60", "9:00", "18:0", " 18:00", "18:00\n", "18:00:00", "noon", ""])
def test_work_end_time_rejects_bad_clock_times(value: str) -> None:
    """Reject malformed and out-of-range times before services are constructed."""
    with pytest.raises(ValidationError):
        TemplateRenderConfig(work_end_time=value)
