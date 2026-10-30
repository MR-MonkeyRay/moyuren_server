"""业务时间下班配置的严格校验。"""

import pytest
from pydantic import ValidationError

from app.core.config import TemplateRenderConfig


def test_work_end_time_defaults_to_eighteen() -> None:
    """默认值由配置提供，并兼容旧 YAML。"""
    assert TemplateRenderConfig().work_end_time == "18:00"


@pytest.mark.parametrize("value", ["00:00", "09:05", "18:00", "23:59"])
def test_work_end_time_accepts_valid_hh_mm(value: str) -> None:
    """只接受补零的业务时钟时间。"""
    assert TemplateRenderConfig(work_end_time=value).work_end_time == value


@pytest.mark.parametrize("value", ["24:00", "18:60", "9:00", "18:0", " 18:00", "18:00\n", "18:00:00", "noon", ""])
def test_work_end_time_rejects_bad_clock_times(value: str) -> None:
    """在服务构建前就拒绝格式错误和越界的时间。"""
    with pytest.raises(ValidationError):
        TemplateRenderConfig(work_end_time=value)
