"""Tests for app/services/renderer.py - image rendering service."""

import hashlib
import json
import time
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from markupsafe import Markup

from app.core.config import TemplateItemConfig, TemplateRenderConfig, TemplatesConfig, ViewportConfig
from app.services.renderer import ImageRenderer, format_datetime, nl2br


def _resource_cache_key(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


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
                TemplateItemConfig(name="test", path=str(template_file), viewport=ViewportConfig(width=800, height=600))
            ],
        )

    @pytest.fixture
    def render_config(self) -> TemplateRenderConfig:
        """Create a render configuration."""
        return TemplateRenderConfig(device_scale_factor=2, jpeg_quality=90, use_china_cdn=False)

    @pytest.fixture
    def renderer(
        self, templates_config: TemplatesConfig, render_config: TemplateRenderConfig, tmp_path: Path, logger
    ) -> ImageRenderer:
        """Create an ImageRenderer instance."""
        images_dir = tmp_path / "static"
        images_dir.mkdir()
        return ImageRenderer(
            templates_config=templates_config, images_dir=str(images_dir), render_config=render_config, logger=logger
        )

    def test_renderer_creates_images_dir(
        self, templates_config: TemplatesConfig, render_config: TemplateRenderConfig, tmp_path: Path, logger
    ) -> None:
        """Test renderer creates static directory if not exists."""
        images_dir = tmp_path / "new_static"
        ImageRenderer(
            templates_config=templates_config, images_dir=str(images_dir), render_config=render_config, logger=logger
        )
        assert images_dir.exists()

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
        mock_page.evaluate = AsyncMock(return_value=1123)
        mock_page.set_viewport_size = AsyncMock()

        with patch.object(renderer, "_get_jinja_env") as mock_env:
            mock_template = MagicMock()
            mock_template.render.return_value = "<html>rendered</html>"
            mock_env.return_value.get_template.return_value = mock_template

            with patch("app.services.renderer.browser_manager") as mock_browser:
                mock_browser.create_page = AsyncMock(return_value=mock_page)
                mock_browser.release_page = AsyncMock()

                filename = await renderer.render(template_data)

        assert filename.endswith(".jpg")
        # Template name is "test", so filename starts with "test_"
        assert "test_" in filename
        mock_page.route.assert_awaited_once()
        mock_page.set_content.assert_awaited_once_with("<html>rendered</html>", wait_until="networkidle")

    @pytest.mark.asyncio
    async def test_render_with_template_name(self, renderer: ImageRenderer) -> None:
        """Test render with specific template name."""
        template_data = {"title": "Test"}

        mock_page = AsyncMock()
        mock_page.set_content = AsyncMock()
        mock_page.screenshot = AsyncMock(return_value=b"fake")
        mock_page.close = AsyncMock()
        mock_page.evaluate = AsyncMock(return_value=1123)
        mock_page.set_viewport_size = AsyncMock()

        with patch.object(renderer, "_get_jinja_env") as mock_env:
            mock_template = MagicMock()
            mock_template.render.return_value = "<html></html>"
            mock_env.return_value.get_template.return_value = mock_template

            with patch("app.services.renderer.browser_manager") as mock_browser:
                mock_browser.create_page = AsyncMock(return_value=mock_page)
                mock_browser.release_page = AsyncMock()

                filename = await renderer.render(template_data, template_name="test")

        assert filename is not None

    def test_should_cache_google_font_resources(self, renderer: ImageRenderer) -> None:
        """Test remote font resources are selected for caching."""
        assert renderer._should_cache_resource("https://fonts.googleapis.cn/css2?family=Test", "stylesheet")
        assert renderer._should_cache_resource("https://fonts.gstatic.cn/s/font.woff2", "font")
        assert not renderer._should_cache_resource("https://example.com/app.css", "stylesheet")
        assert not renderer._should_cache_resource("https://fonts.googleapis.cn/css2?family=Test", "document")

    @pytest.mark.asyncio
    async def test_get_remote_resource_uses_fresh_cache(self, renderer: ImageRenderer) -> None:
        """Test fresh cached render resources are served without refetching."""
        url = "https://fonts.googleapis.cn/css2?family=Test"
        cache_key = _resource_cache_key(url)
        body_path = renderer.resource_cache_dir / f"{cache_key}.body"
        meta_path = renderer.resource_cache_dir / f"{cache_key}.meta"
        body_path.write_bytes(b"body { font-family: Test; }")
        meta_path.write_text(json.dumps({"url": url, "content_type": "text/css; charset=utf-8", "fetched_at": time.time()}), encoding="utf-8")

        with patch.object(renderer, "_fetch_remote_resource_async") as fetch:
            cached = await renderer._get_remote_resource(url, ttl=3600, timeout=1.0)

        assert cached is not None
        assert cached.body == b"body { font-family: Test; }"
        assert cached.content_type == "text/css; charset=utf-8"
        assert cached.cache_state == "cache hit"
        fetch.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_remote_resource_uses_stale_cache_on_fetch_failure(self, renderer: ImageRenderer) -> None:
        """Test stale render resources are used when refresh fails."""
        url = "https://fonts.gstatic.cn/s/font.woff2"
        cache_key = _resource_cache_key(url)
        body_path = renderer.resource_cache_dir / f"{cache_key}.body"
        meta_path = renderer.resource_cache_dir / f"{cache_key}.meta"
        body_path.write_bytes(b"font-bytes")
        meta_path.write_text(json.dumps({"url": url, "content_type": "font/woff2", "fetched_at": time.time() - 7200}), encoding="utf-8")

        with patch.object(renderer, "_fetch_remote_resource_async", side_effect=TimeoutError("timeout")):
            cached = await renderer._get_remote_resource(url, ttl=3600, timeout=1.0)

        assert cached is not None
        assert cached.body == b"font-bytes"
        assert cached.content_type == "font/woff2"
        assert cached.cache_state == "stale cache"

    @pytest.mark.asyncio
    async def test_install_resource_cache_routes_disabled(self, renderer: ImageRenderer) -> None:
        """Test cache routes are skipped when disabled by config."""
        renderer.render_config.remote_resource_cache_enabled = False
        page = AsyncMock()

        await renderer._install_resource_cache_routes(page)

        page.route.assert_not_called()

    def test_rewrite_css_urls_matches_standard_syntax(self, renderer: ImageRenderer) -> None:
        """Test CSS URL rewrite regex correctly handles standard CSS syntax."""
        base_url = "https://fonts.googleapis.cn/css2?family=Test"

        # url(path) - unquoted relative path should be rewritten
        result = renderer._rewrite_css_urls(
            b"body { src: url(font.woff2) }", base_url, "text/css; charset=utf-8"
        )
        assert "url(https://fonts.googleapis.cn/font.woff2)" in result.decode()

        # url('path') - single-quoted relative path should be rewritten
        result = renderer._rewrite_css_urls(
            b"body { src: url('font.woff2') }", base_url, "text/css; charset=utf-8"
        )
        assert "url('https://fonts.googleapis.cn/font.woff2')" in result.decode()

        # url("path") - double-quoted relative path should be rewritten
        result = renderer._rewrite_css_urls(
            b"body { src: url(\"font.woff2\") }", base_url, "text/css; charset=utf-8"
        )
        assert "font.woff2" not in result.decode() or "https://" in result.decode()

        # url(data:...) - data URI should NOT be rewritten
        result = renderer._rewrite_css_urls(
            b"body { src: url(data:image/svg+xml;base64,abc) }", base_url, "text/css; charset=utf-8"
        )
        assert "data:image/svg+xml" in result.decode()

        # url(https://...) - absolute URL should NOT be rewritten
        result = renderer._rewrite_css_urls(
            b"body { src: url(https://example.com/font.woff2) }", base_url, "text/css; charset=utf-8"
        )
        assert "https://example.com/font.woff2" in result.decode()

    @pytest.mark.asyncio
    async def test_fetch_remote_resource_rejects_oversized_response(self, renderer: ImageRenderer) -> None:
        """Test oversized remote resource responses are rejected via content-length."""
        url = "https://fonts.googleapis.cn/css2?family=Test"

        # Test content-length exceeding limit
        mock_response = MagicMock()
        mock_response.headers = {"content-type": "text/css", "content-length": str(10 * 1024 * 1024)}
        mock_response.raise_for_status = MagicMock()
        mock_response.aiter_bytes = MagicMock(return_value=iter([]))

        with patch("app.services.renderer.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.stream = MagicMock()

            # Mock stream context manager
            stream_cm = MagicMock()
            stream_cm.__aenter__ = AsyncMock(return_value=mock_response)
            stream_cm.__aexit__ = AsyncMock(return_value=None)
            mock_client.stream.return_value = stream_cm

            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            with pytest.raises(ValueError, match="too large"):
                await renderer._fetch_remote_resource_async(url, timeout=5.0)

    @pytest.mark.asyncio
    async def test_fetch_remote_resource_reads_async_stream(self, renderer: ImageRenderer) -> None:
        """Test remote resources are read from httpx async byte streams."""
        url = "https://fonts.googleapis.cn/css2?family=Test"

        async def chunks():
            yield b"body { "
            yield b"font-family: Test; }"

        mock_response = MagicMock()
        mock_response.headers = {"content-type": "text/css"}
        mock_response.raise_for_status = MagicMock()
        mock_response.aiter_bytes = MagicMock(return_value=chunks())

        with patch("app.services.renderer.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.stream = MagicMock()

            stream_cm = MagicMock()
            stream_cm.__aenter__ = AsyncMock(return_value=mock_response)
            stream_cm.__aexit__ = AsyncMock(return_value=None)
            mock_client.stream.return_value = stream_cm

            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            content_type, body = await renderer._fetch_remote_resource_async(url, timeout=5.0)

        assert content_type == "text/css"
        assert body == b"body { font-family: Test; }"

    @pytest.mark.asyncio
    async def test_install_resource_cache_routes_adds_cors_for_cached_fonts(self, renderer: ImageRenderer) -> None:
        """Test cached font responses include CORS headers required by Chromium."""
        page = AsyncMock()
        await renderer._install_resource_cache_routes(page)

        route = AsyncMock()
        request = MagicMock()
        request.url = "https://fonts.gstatic.cn/s/font.woff2"
        request.resource_type = "font"
        route.request = request

        cached = MagicMock()
        cached.body = b"font-bytes"
        cached.content_type = "font/woff2"
        cached.cache_state = "cache hit"

        with patch.object(renderer, "_get_remote_resource", return_value=cached):
            handle_route = page.route.call_args[0][1]
            await handle_route(route)

        route.fulfill.assert_awaited_once()
        headers = route.fulfill.await_args.kwargs["headers"]
        assert headers["Cache-Control"].startswith("public, max-age=")
        assert headers["Access-Control-Allow-Origin"] == "*"

    def test_write_cached_resource_atomic(self, renderer: ImageRenderer) -> None:
        """Test cached resource writes use atomic approach and JSON metadata."""
        url = "https://fonts.googleapis.cn/css2?family=Test"
        body = b"body { font-family: Test; }"
        fetched_at = time.time()
        content_type = "text/css; charset=utf-8"

        cache_key = _resource_cache_key(url)
        body_path = renderer.resource_cache_dir / f"{cache_key}.body"
        meta_path = renderer.resource_cache_dir / f"{cache_key}.meta"

        renderer._write_cached_resource(body_path, meta_path, url, content_type, body, fetched_at)

        assert body_path.exists()
        assert body_path.read_bytes() == body
        assert meta_path.exists()
        meta_data = json.loads(meta_path.read_text(encoding="utf-8"))
        assert meta_data["url"] == url
        assert meta_data["content_type"] == content_type
        assert meta_data["fetched_at"] == fetched_at
        assert "stored_at" in meta_data
        assert meta_data["size"] == len(body)

    def test_read_cached_resource_meta_handles_old_format(self, renderer: ImageRenderer) -> None:
        """Test old newline-separated meta format is handled gracefully."""
        cache_key = "old_format_test"
        meta_path = renderer.resource_cache_dir / f"{cache_key}.meta"
        # Write old-format (newline-separated) meta file
        meta_path.write_text("https://example.com\ntext/css\n1234567890.0\n", encoding="utf-8")

        result = renderer._read_cached_resource_meta(meta_path)
        assert result is None

    @pytest.mark.asyncio
    async def test_render_degraded_flag_set_on_empty_stylesheet(self, renderer: ImageRenderer) -> None:
        """Test _render_degraded is set when empty stylesheet fallback is used."""
        renderer._render_degraded = False

        with patch.object(renderer, "_get_remote_resource", return_value=None):
            page = AsyncMock()
            await renderer._install_resource_cache_routes(page)

            # Simulate a stylesheet request hitting the fallback
            route = AsyncMock()
            request = MagicMock()
            request.url = "https://fonts.googleapis.cn/css2?family=Test"
            request.resource_type = "stylesheet"
            route.request = request

            # Find the handle_route callback from page.route call
            handle_route = page.route.call_args[0][1]
            await handle_route(route)

        assert renderer._render_degraded is True

    def test_sanitize_url_for_log(self, renderer: ImageRenderer) -> None:
        """Test URL sanitization strips query parameters."""
        url = "https://fonts.googleapis.cn/css2?family=Test&display=swap"
        sanitized = renderer._sanitize_url_for_log(url)
        assert "family=Test" not in sanitized
        assert "display=swap" not in sanitized
        assert "fonts.googleapis.cn" in sanitized
