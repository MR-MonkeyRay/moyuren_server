"""模板主题渲染与配色可访问性测试。"""

import re
from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader

from app.services.compute import DataComputer
from app.services.renderer import format_datetime


TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "templates"
THEME_NAMES = (
    "spring_festival",
    "mid_autumn",
    "national_day",
    "spring",
    "summer",
    "autumn",
    "winter",
)
THEME_BLOCK_PATTERN = re.compile(r"body\.theme-([a-z][a-z0-9_]*)\s*\{([^}]+)\}", re.DOTALL)
TOKEN_PATTERN = re.compile(r"--([a-z0-9-]+)\s*:\s*(#[0-9A-Fa-f]{6})\s*;")
CONTRAST_PAIRS = {
    "moyuren.html": (
        ("text-dark", "bg-paper"),
        ("text-grey", "bg-paper"),
        ("text-light", "bg-paper"),
        ("blue-main", "blue-bg"),
        ("green-main", "green-bg"),
        ("red-main", "red-bg"),
        ("orange-main", "orange-bg"),
    ),
    "moyuren_cute.html": (
        ("line-coffee", "bg-cream"),
        ("line-coffee", "card-bg"),
        ("line-coffee", "m-pink"),
        ("line-coffee", "m-mint"),
        ("line-coffee", "m-peach"),
        ("line-coffee", "m-lilac"),
        ("line-coffee", "m-gray"),
        ("line-coffee", "m-gold-bg"),
        ("m-soft-red", "card-bg"),
        ("color-up", "card-bg"),
        ("color-down", "card-bg"),
    ),
}


def _relative_luminance(color: str) -> float:
    """返回六位十六进制颜色的 WCAG 相对亮度。"""
    channels = [int(color[index : index + 2], 16) / 255 for index in (1, 3, 5)]
    linear = [
        value / 12.92
        if value <= 0.04045
        else ((value + 0.055) / 1.055) ** 2.4
        for value in channels
    ]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast_ratio(foreground: str, background: str) -> float:
    """返回两个十六进制颜色之间的 WCAG 对比度。"""
    lighter, darker = sorted(
        (_relative_luminance(foreground), _relative_luminance(background)), reverse=True
    )
    return (lighter + 0.05) / (darker + 0.05)


def _render(template_name: str, *, include_theme: bool, theme_name: str = "spring") -> str:
    """使用正常计算得到的兜底上下文渲染正式模板。"""
    env = Environment(loader=FileSystemLoader(TEMPLATES_DIR), autoescape=True)
    env.filters["format_datetime"] = format_datetime
    context = DataComputer().compute({})
    if include_theme:
        context["theme"] = {"name": theme_name, "source": "立春", "kind": "season"}
    else:
        context.pop("theme", None)
    return env.get_template(template_name).render(context)


@pytest.mark.parametrize("template_name", ["moyuren.html", "moyuren_cute.html"])
def test_template_body_uses_selected_theme(template_name: str) -> None:
    """将计算得到的主题名传到各模板的 body 上。"""
    html = _render(template_name, include_theme=True, theme_name="mid_autumn")

    assert '<body class="theme-mid_autumn">' in html


@pytest.mark.parametrize("template_name", ["moyuren.html", "moyuren_cute.html"])
def test_template_body_defaults_when_theme_is_missing(template_name: str) -> None:
    """缺少主题的旧上下文仍能使用默认配色渲染。"""
    html = _render(template_name, include_theme=False)

    assert '<body class="theme-default">' in html


@pytest.mark.parametrize("template_name", ["moyuren.html", "moyuren_cute.html"])
def test_unknown_theme_uses_default_tokens(template_name: str) -> None:
    """输出合法的未知主题类名，不额外定义对应的 CSS 配色。"""
    html = _render(template_name, include_theme=True, theme_name="custom_brand")
    css = (TEMPLATES_DIR / template_name).read_text(encoding="utf-8")

    assert '<body class="theme-custom_brand">' in html
    assert "body.theme-custom_brand {" not in css


@pytest.mark.parametrize("template_name", ["moyuren.html", "moyuren_cute.html"])
def test_theme_palette_text_contrast_is_wcag_aa(template_name: str) -> None:
    """要求每套配色中的普通及语义文本组合均达到 WCAG AA。"""
    css = (TEMPLATES_DIR / template_name).read_text(encoding="utf-8")
    root_match = re.search(r":root\s*\{([^}]+)\}", css, re.DOTALL)
    assert root_match is not None
    root_tokens = dict(TOKEN_PATTERN.findall(root_match.group(1)))
    blocks = {
        name: root_tokens | dict(TOKEN_PATTERN.findall(content))
        for name, content in THEME_BLOCK_PATTERN.findall(css)
    }

    assert set(blocks) == set(THEME_NAMES)
    for theme_name, tokens in blocks.items():
        for text_token, background_token in CONTRAST_PAIRS[template_name]:
            ratio = _contrast_ratio(tokens[text_token], tokens[background_token])
            assert ratio >= 4.5, (
                f"{template_name} {theme_name} {text_token}/{background_token} "
                f"contrast is {ratio:.2f}:1"
            )


def test_default_palette_text_contrast_is_wcag_aa() -> None:
    """确保各模板的默认配色达到 WCAG AA。"""
    root_pattern = re.compile(r":root\s*\{([^}]+)\}", re.DOTALL)

    for template_name, pairs in CONTRAST_PAIRS.items():
        css = (TEMPLATES_DIR / template_name).read_text(encoding="utf-8")
        root_match = root_pattern.search(css)
        assert root_match is not None
        tokens = dict(TOKEN_PATTERN.findall(root_match.group(1)))
        for text_token, background_token in pairs:
            ratio = _contrast_ratio(tokens[text_token], tokens[background_token])
            assert ratio >= 4.5, (
                f"{template_name} default {text_token}/{background_token} "
                f"contrast is {ratio:.2f}:1"
            )


def test_main_secondary_labels_and_fallback_banner_are_wcag_aa() -> None:
    """检查实际节假日渐变背景和橙色兜底横幅上的文本对比度。"""
    css = (TEMPLATES_DIR / "moyuren.html").read_text(encoding="utf-8")
    root_match = re.search(r":root\s*\{([^}]+)\}", css, re.DOTALL)
    assert root_match is not None
    root_tokens = dict(TOKEN_PATTERN.findall(root_match.group(1)))
    blocks = [("default", ""), *THEME_BLOCK_PATTERN.findall(css)]
    for theme_name, content in blocks:
        tokens = root_tokens | dict(TOKEN_PATTERN.findall(content))
        for background in ("#FFEDD5", "#FEE2E2"):
            ratio = _contrast_ratio(tokens["text-light"], background)
            assert ratio >= 4.5, f"{theme_name} secondary label contrast is {ratio:.2f}:1"
        for background in ("#ECFDF5", "#D1FAE5"):
            assert _contrast_ratio(tokens["green-main"], background) >= 4.5
    for selector in ("fallback-title", "fallback-desc"):
        match = re.search(rf"^\s*\.{selector}\s*\{{([^}}]+)\}}", css, re.DOTALL | re.MULTILINE)
        assert match is not None
        color_match = re.search(r"color:\s*(#[0-9A-Fa-f]{6})", match.group(1))
        assert color_match is not None
        for background in ("#FFF3E0", "#FFE0B2"):
            assert _contrast_ratio(color_match.group(1), background) >= 4.5


@pytest.mark.parametrize("template_name", ["moyuren.html", "moyuren_cute.html"])
def test_theme_palettes_keep_chinese_market_colors(template_name: str) -> None:
    """确保所有主题保持涨红跌绿。"""
    css = (TEMPLATES_DIR / template_name).read_text(encoding="utf-8")
    blocks = {
        name: dict(TOKEN_PATTERN.findall(content))
        for name, content in THEME_BLOCK_PATTERN.findall(css)
    }

    for tokens in blocks.values():
        if template_name == "moyuren.html":
            assert tokens["red-main"].upper() == "#B91C1C"
            assert tokens["green-main"].upper() == "#047857"
        else:
            assert tokens["color-up"].upper() == "#B91C1C"
            assert tokens["color-down"].upper() == "#047857"
