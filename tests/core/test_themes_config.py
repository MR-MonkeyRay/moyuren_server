"""Configuration validation for CSS-safe theme names."""

import pytest
from pydantic import ValidationError

from app.core.config import TemplateRenderConfig, ThemesConfig


def test_theme_defaults_are_backward_compatible() -> None:
    """Old template configuration receives independent default mappings."""
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
    """Unsafe CSS class values fail at the Pydantic configuration boundary."""
    with pytest.raises(ValidationError, match="theme names must match"):
        TemplateRenderConfig(themes=ThemesConfig(festival_themes={"春节": name}))


@pytest.mark.parametrize("name", ["spring", "custom_moon_2", "a0", "default"])
def test_accept_custom_css_safe_names(name: str) -> None:
    """Palette existence is deliberately left to the chosen template."""
    assert ThemesConfig(festival_themes={"春节": name}).festival_themes["春节"] == name
