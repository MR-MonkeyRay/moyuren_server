"""Tests for app/services/renderer.py - image rendering service."""

from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from markupsafe import Markup

from app.core.config import TemplateItemConfig, TemplateRenderConfig, TemplatesConfig, ViewportConfig
from app.services.renderer import ImageRenderer, format_datetime, nl2br


class TestFormatDatetime:
    """Tests for format_datetime function."""

    def test_new_format_string(self) -> None:
        """Test new format string (YYYY/MM/DD HH:MM:SS)."""
        result = format_datetime("2026/02/04 10:30:00")
        assert result == "2026-02-04 10:30"

    def test_rfc3339_string(self) -> None:
        """Test RFC3339 string formatting."""
        result = format_datetime("2026-02-04T10:30:00+08:00")
        assert result == "2026-02-04 10:30"

    def test_rfc3339_with_z_suffix(self) -> None:
        """Test RFC3339 string with Z suffix."""
        result = format_datetime("2026-02-04T02:30:00Z")
        assert result == "2026-02-04 02:30"

    def test_datetime_object(self) -> None:
        """Test datetime object formatting."""
        dt = datetime(2026, 2, 4, 10, 30, 0)
        result = format_datetime(dt)
        assert result == "2026-02-04 10:30"

    def test_unix_timestamp_int(self) -> None:
        """Test Unix timestamp (int) formatting."""
        # 2026-02-04 10:30:00 UTC+8 = 1738635000
        timestamp = 1738635000
        result = format_datetime(timestamp)
        # Result depends on local timezone, just check format
        assert len(result) == 16  # "YYYY-MM-DD HH:MM"

    def test_unix_timestamp_float(self) -> None:
        """Test Unix timestamp (float) formatting."""
        timestamp = 1738635000.5
        result = format_datetime(timestamp)
        assert len(result) == 16

    def test_none_returns_placeholder(self) -> None:
        """Test None returns placeholder."""
        result = format_datetime(None)
        assert result == "--"

    def test_empty_string_returns_placeholder(self) -> None:
        """Test empty string returns placeholder."""
        result = format_datetime("")
        assert result == "--"

    def test_whitespace_string_returns_placeholder(self) -> None:
        """Test whitespace string returns placeholder."""
        result = format_datetime("   ")
        assert result == "--"

    def test_invalid_string_returns_placeholder(self) -> None:
        """Test invalid string returns placeholder."""
        result = format_datetime("not a date")
        assert result == "--"

    def test_bool_returns_placeholder(self) -> None:
        """Test bool (subclass of int) returns placeholder."""
        result = format_datetime(True)
        assert result == "--"

    def test_invalid_timestamp_returns_placeholder(self) -> None:
        """Test invalid timestamp returns placeholder."""
        result = format_datetime(99999999999999)  # Too large
        assert result == "--"


class TestNl2br:
    """Tests for nl2br function."""

    def test_converts_newlines(self) -> None:
        """Test converts newlines to br tags."""
        result = nl2br("Line1\nLine2")
        assert "<br>" in str(result)
        assert isinstance(result, Markup)

    def test_escapes_html(self) -> None:
        """Test escapes HTML special characters."""
        result = nl2br("<script>alert('xss')</script>")
        assert "<script>" not in str(result)
        assert "&lt;script&gt;" in str(result)

    def test_none_returns_empty(self) -> None:
        """Test None returns empty Markup."""
        result = nl2br(None)
        assert str(result) == ""

    def test_empty_string_returns_empty(self) -> None:
        """Test empty string returns empty Markup."""
        result = nl2br("")
        assert str(result) == ""


class TestImageRenderer:
    """Tests for ImageRenderer class."""

    @pytest.fixture
    def templates_config(self, tmp_path: Path) -> TemplatesConfig:
        """Create a templates configuration."""
        # Create a simple template file
        template_dir = tmp_path / "templates"
        template_dir.mkdir()
        template_file = template_dir / "test.html"
        template_file.write_text("<html><body>{{ title }}</body></html>")

        return TemplatesConfig(
            default="test",
            items=[
                TemplateItemConfig(
                    name="test",
                    path=str(template_file),
                    viewport=ViewportConfig(width=800, height=600)
                )
            ]
        )

    @pytest.fixture
    def render_config(self) -> TemplateRenderConfig:
        """Create a render configuration."""
        return TemplateRenderConfig(
            device_scale_factor=2,
            jpeg_quality=90,
            use_china_cdn=False
        )

    @pytest.fixture
    def renderer(
        self,
        templates_config: TemplatesConfig,
        render_config: TemplateRenderConfig,
        tmp_path: Path,
        logger
    ) -> ImageRenderer:
        """Create an ImageRenderer instance."""
        static_dir = tmp_path / "static"
        static_dir.mkdir()
        return ImageRenderer(
            templates_config=templates_config,
            static_dir=str(static_dir),
            render_config=render_config,
            logger=logger
        )

    def test_renderer_creates_static_dir(
        self,
        templates_config: TemplatesConfig,
        render_config: TemplateRenderConfig,
        tmp_path: Path,
        logger
    ) -> None:
        """Test renderer creates static directory if not exists."""
        static_dir = tmp_path / "new_static"
        ImageRenderer(
            templates_config=templates_config,
            static_dir=str(static_dir),
            render_config=render_config,
            logger=logger
        )
        assert static_dir.exists()

    def test_get_jinja_env(self, renderer: ImageRenderer, templates_config: TemplatesConfig) -> None:
        """Test _get_jinja_env returns Environment."""
        template_item = templates_config.get_template("test")
        env = renderer._get_jinja_env(template_item.path)

        assert env is not None
        # Check custom filters are registered
        assert "format_datetime" in env.filters
        assert "nl2br" in env.filters

    def test_get_jinja_env_caches_environment(self, renderer: ImageRenderer, templates_config: TemplatesConfig) -> None:
        """Test _get_jinja_env caches Environment."""
        template_item = templates_config.get_template("test")

        env1 = renderer._get_jinja_env(template_item.path)
        env2 = renderer._get_jinja_env(template_item.path)

        assert env1 is env2

    @pytest.mark.asyncio
    async def test_render_success(self, renderer: ImageRenderer) -> None:
        """Test successful render."""
        template_data = {"title": "Test Title"}

        # Mock browser_manager
        mock_page = AsyncMock()
        mock_page.set_content = AsyncMock()
        mock_page.screenshot = AsyncMock(return_value=b"fake image bytes")
        mock_page.close = AsyncMock()

        with patch.object(
            renderer, "_get_jinja_env"
        ) as mock_env:
            mock_template = MagicMock()
            mock_template.render.return_value = "<html>rendered</html>"
            mock_env.return_value.get_template.return_value = mock_template

            with patch(
                "app.services.renderer.browser_manager"
            ) as mock_browser:
                mock_browser.create_page = AsyncMock(return_value=mock_page)
                mock_browser.release_page = AsyncMock()

                filename = await renderer.render(template_data)

        assert filename.endswith(".jpg")
        # Template name is "test", so filename starts with "test_"
        assert "test_" in filename

    @pytest.mark.asyncio
    async def test_render_with_template_name(self, renderer: ImageRenderer) -> None:
        """Test render with specific template name."""
        template_data = {"title": "Test"}

        mock_page = AsyncMock()
        mock_page.set_content = AsyncMock()
        mock_page.screenshot = AsyncMock(return_value=b"fake")
        mock_page.close = AsyncMock()

        with patch.object(renderer, "_get_jinja_env") as mock_env:
            mock_template = MagicMock()
            mock_template.render.return_value = "<html></html>"
            mock_env.return_value.get_template.return_value = mock_template

            with patch("app.services.renderer.browser_manager") as mock_browser:
                mock_browser.create_page = AsyncMock(return_value=mock_page)
                mock_browser.release_page = AsyncMock()

                filename = await renderer.render(template_data, template_name="test")

        assert filename is not None
