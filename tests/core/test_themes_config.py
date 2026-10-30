"""CSS 安全主题名的配置校验。"""

import pytest
from pydantic import ValidationError

from app.core.config import TemplateRenderConfig, ThemesConfig


def test_theme_defaults_are_backward_compatible() -> None:
    """旧模板配置能获得各自独立的默认映射。"""
    config = TemplateRenderConfig().themes
    assert config.enabled is True
    assert config.season_themes is True
    assert config.festival_themes == {
        "除夕": "spring_festival", "春节": "spring_festival", "元宵节": "spring_festival",
        "中秋节": "mid_autumn", "国庆节": "national_day",
    }
    config.festival_themes.clear()
    assert ThemesConfig().festival_themes


@pytest.mark.parametrize("name", ["", "Spring", "_spring", "2spring", "spring-festival", "spring moon", "春", "spring\n", 'spring" onclick="x'])
def test_reject_invalid_theme_names(name: str) -> None:
    """不安全的 CSS class 值在 Pydantic 配置边界即被拒绝。"""
    with pytest.raises(ValidationError, match="theme names must match"):
        TemplateRenderConfig(themes=ThemesConfig(festival_themes={"春节": name}))


@pytest.mark.parametrize("name", ["spring", "custom_moon_2", "a0", "default"])
def test_accept_custom_css_safe_names(name: str) -> None:
    """配色是否存在，刻意交由所选模板自行决定。"""
    assert ThemesConfig(festival_themes={"春节": name}).festival_themes["春节"] == name
