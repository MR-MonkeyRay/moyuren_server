"""Tests for app/services/renderer.py - image rendering service."""

import asyncio
import hashlib
import json
import logging
import time
from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import httpx
import pytest
from freezegun import freeze_time
from markupsafe import Markup

from app.core.config import (
    TemplateItemConfig,
    TemplateRenderConfig,
    TemplatesConfig,
    ViewportConfig,
)
from app.core.errors import RenderError
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

    def test_filename_defaults_to_business_time(self, renderer: ImageRenderer) -> None:
        """Test that filenames use the business date when UTC is still yesterday."""
        with (
            freeze_time("2026-02-04T18:00:00+00:00"),
            patch("app.services.calendar._business_timezone", ZoneInfo("Asia/Shanghai")),
        ):
            assert renderer._generate_filename("test") == "test_20260205_020000.jpg"

    @pytest.mark.asyncio
    async def test_render_filename_uses_snapshot(self, renderer: ImageRenderer) -> None:
        """Test that rendering preserves the provided timestamp in the filename."""
        snapshot = datetime(2026, 2, 5, 2, tzinfo=ZoneInfo("Asia/Shanghai"))
        with (
            freeze_time("2026-02-06T00:00:00+00:00"),
            patch.object(renderer, "_generate_screenshot", new=AsyncMock(return_value=b"jpeg")),
            patch("app.services.renderer.now_business") as mock_now,
        ):
            filename = await renderer.render({"title": "test"}, "test", now=snapshot)

        assert filename == "test_20260205_020000.jpg"
        assert (renderer.images_dir / filename).read_bytes() == b"jpeg"
        mock_now.assert_not_called()

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
                    viewport=ViewportConfig(width=800, height=600),
                )
            ],
        )

    @pytest.fixture
    def render_config(self) -> TemplateRenderConfig:
        """Create a render configuration."""
        return TemplateRenderConfig(
            device_scale_factor=2, jpeg_quality=90, use_china_cdn=False
        )

    @pytest.fixture
    def renderer(
        self,
        templates_config: TemplatesConfig,
        render_config: TemplateRenderConfig,
        tmp_path: Path,
        logger,
    ) -> ImageRenderer:
        """Create an ImageRenderer instance."""
        images_dir = tmp_path / "static"
        images_dir.mkdir()
        return ImageRenderer(
            templates_config=templates_config,
            images_dir=str(images_dir),
            render_config=render_config,
            logger=logger,
        )

    def test_renderer_creates_images_dir(
        self,
        templates_config: TemplatesConfig,
        render_config: TemplateRenderConfig,
        tmp_path: Path,
        logger,
    ) -> None:
        """Test renderer creates static directory if not exists."""
        images_dir = tmp_path / "new_static"
        ImageRenderer(
            templates_config=templates_config,
            images_dir=str(images_dir),
            render_config=render_config,
            logger=logger,
        )
        assert images_dir.exists()

    def test_get_jinja_env(
        self, renderer: ImageRenderer, templates_config: TemplatesConfig
    ) -> None:
        """Test _get_jinja_env returns Environment."""
        template_item = templates_config.get_template("test")
        env = renderer._get_jinja_env(template_item.path)

        assert env is not None
        # Check custom filters are registered
        assert "format_datetime" in env.filters
        assert "nl2br" in env.filters

    def test_get_jinja_env_caches_environment(
        self, renderer: ImageRenderer, templates_config: TemplatesConfig
    ) -> None:
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
        mock_page.on = MagicMock()
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
        mock_page.set_content.assert_awaited_once_with(
            "<html>rendered</html>",
            wait_until="domcontentloaded",
            timeout=10000,
        )

    @pytest.mark.asyncio
    async def test_render_with_template_name(self, renderer: ImageRenderer) -> None:
        """Test render with specific template name."""
        template_data = {"title": "Test"}

        mock_page = AsyncMock()
        mock_page.set_content = AsyncMock()
        mock_page.screenshot = AsyncMock(return_value=b"fake")
        mock_page.close = AsyncMock()
        mock_page.on = MagicMock()
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

    @pytest.mark.asyncio
    async def test_wait_for_render_ready_timeout_continues(
        self, renderer: ImageRenderer
    ) -> None:
        """Test render readiness timeout is degraded instead of failing the render."""
        page = AsyncMock()
        page.evaluate = AsyncMock(side_effect=TimeoutError("font wait"))

        assert await renderer._wait_for_render_ready(page, "test") is False

        page.evaluate.assert_awaited_once()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("status", "ready"),
        [("ready", True), ("unsupported", True), ("timeout", False), ("error", False)],
    )
    async def test_wait_for_render_ready_reports_status(
        self, renderer: ImageRenderer, status: str, ready: bool
    ) -> None:
        """Test readiness reports when pending fonts need to be aborted."""
        page = AsyncMock()
        page.evaluate.return_value = status

        assert await renderer._wait_for_render_ready(page, "test") is ready

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("cache_enabled", "font_url"),
        [
            (False, "https://fonts.gstatic.com/test.woff2"),
            (True, "https://fonts.gstatic.com/test.woff2"),
            (True, "https://cdn.fonts.gstatic.com/test.woff2"),
        ],
    )
    async def test_render_aborts_pending_and_late_fonts_before_screenshot(
        self, renderer: ImageRenderer, cache_enabled: bool, font_url: str
    ) -> None:
        """Test readiness expiry cancels font fetches before capturing fallbacks."""
        renderer.render_config.remote_resource_cache_enabled = cache_enabled
        renderer.render_config.page_load_timeout_sec = 3.25
        page = AsyncMock()
        page.on = MagicMock()
        page.evaluate.side_effect = ["timeout", 600]
        route = AsyncMock()
        route.request = MagicMock(url=font_url, resource_type="font")
        late_route = AsyncMock()
        late_route.request = MagicMock(url=font_url, resource_type="font")
        started = asyncio.Event()
        cancelled = asyncio.Event()
        handlers: list[asyncio.Task[None]] = []

        async def hanging_fetch(*args: object, **kwargs: object) -> None:
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        async def load_html(*args: object, **kwargs: object) -> None:
            handle_route = page.route.call_args.args[1]
            handlers.append(asyncio.create_task(handle_route(route)))
            await asyncio.wait_for(started.wait(), timeout=1)

        async def capture(**kwargs: object) -> bytes:
            route.abort.assert_awaited_once()
            assert cancelled.is_set()
            await page.route.call_args.args[1](late_route)
            late_route.abort.assert_awaited_once()
            late_route.fetch.assert_not_awaited()
            return b"fallback jpeg"

        route.fetch.side_effect = hanging_fetch
        page.set_content.side_effect = load_html
        page.screenshot.side_effect = capture
        with (
            patch.object(renderer, "_get_remote_resource", side_effect=hanging_fetch),
            patch("app.services.renderer.browser_manager") as manager,
        ):
            manager.create_page = AsyncMock(return_value=page)
            manager.release_page = AsyncMock()
            try:
                filename = await asyncio.wait_for(renderer.render({}), timeout=2)
            finally:
                await asyncio.gather(*handlers)

        assert (renderer.images_dir / filename).read_bytes() == b"fallback jpeg"
        page.route.assert_awaited_once()
        page.screenshot.assert_awaited_once_with(
            type="jpeg", quality=90, full_page=True, timeout=3250
        )
        route.fulfill.assert_not_awaited()
        route.continue_.assert_not_awaited()
        manager.release_page.assert_awaited_once_with(page)

    @pytest.mark.asyncio
    async def test_font_cleanup_failure_preserves_render_error(
        self, renderer: ImageRenderer, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Test abort failures during cleanup preserve the original render failure."""
        renderer.render_config.remote_resource_cache_enabled = False
        page = AsyncMock()
        page.on = MagicMock()
        page.evaluate.side_effect = ["ready", RuntimeError("layout failed")]
        route = AsyncMock()
        route.request = MagicMock(
            url="https://fonts.gstatic.com/font.woff2", resource_type="font"
        )
        route.abort.side_effect = RuntimeError("route already closed")
        started = asyncio.Event()
        handlers: list[asyncio.Task[None]] = []

        async def hanging_fetch(**kwargs: object) -> None:
            started.set()
            await asyncio.Event().wait()

        async def load_html(*args: object, **kwargs: object) -> None:
            handlers.append(
                asyncio.create_task(page.route.call_args.args[1](route))
            )
            await asyncio.wait_for(started.wait(), timeout=1)

        route.fetch.side_effect = hanging_fetch
        page.set_content.side_effect = load_html
        with patch("app.services.renderer.browser_manager") as manager:
            manager.create_page = AsyncMock(return_value=page)
            manager.release_page = AsyncMock()
            try:
                with pytest.raises(RenderError, match="layout failed"):
                    await asyncio.wait_for(renderer.render({}), timeout=2)
            finally:
                await asyncio.gather(*handlers)

        assert "Failed to abort font request" in caplog.text
        route.abort.assert_awaited_once()
        manager.release_page.assert_awaited_once_with(page)

    def test_log_pending_render_resources_includes_resource_url(
        self, renderer: ImageRenderer, caplog
    ) -> None:
        """Test pending render resource logs include enough URL detail to diagnose timeouts."""
        page = MagicMock()
        callbacks = {}
        page.on.side_effect = lambda event, callback: callbacks.__setitem__(
            event, callback
        )
        tracked = renderer._track_page_requests(page)

        request = MagicMock()
        request.url = "https://fonts.googleapis.cn/css2?family=Test&display=swap"
        request.resource_type = "stylesheet"
        callbacks["request"](request)

        with caplog.at_level(logging.WARNING):
            renderer._log_pending_render_resources("test", tracked)

        assert "Pending render resources for test" in caplog.text
        assert (
            "stylesheet fonts.googleapis.cn/css2?family=Test&display=swap"
            in caplog.text
        )

    def test_track_page_requests_logs_failed_resource(
        self, renderer: ImageRenderer, caplog
    ) -> None:
        """Test failed Playwright resource requests are logged with the resource URL."""
        page = MagicMock()
        callbacks = {}
        page.on.side_effect = lambda event, callback: callbacks.__setitem__(
            event, callback
        )
        tracked = renderer._track_page_requests(page)

        request = MagicMock()
        request.url = "https://fonts.gstatic.cn/s/font.woff2"
        request.resource_type = "font"
        callbacks["request"](request)

        with caplog.at_level(logging.WARNING):
            callbacks["requestfailed"](request)

        assert not tracked
        assert (
            "Render resource request failed: font fonts.gstatic.cn/s/font.woff2"
            in caplog.text
        )

    def test_should_cache_google_font_resources(self, renderer: ImageRenderer) -> None:
        """Test remote font resources are selected for caching."""
        assert renderer._should_cache_resource(
            "https://fonts.googleapis.cn/css2?family=Test", "stylesheet"
        )
        assert renderer._should_cache_resource(
            "https://fonts.gstatic.cn/s/font.woff2", "font"
        )
        assert not renderer._should_cache_resource(
            "https://example.com/app.css", "stylesheet"
        )
        assert not renderer._should_cache_resource(
            "https://fonts.googleapis.cn/css2?family=Test", "document"
        )

    @pytest.mark.asyncio
    async def test_get_remote_resource_uses_fresh_cache(
        self, renderer: ImageRenderer
    ) -> None:
        """Test fresh cached render resources are served without refetching."""
        url = "https://fonts.googleapis.cn/css2?family=Test"
        cache_key = _resource_cache_key(url)
        body_path = renderer.resource_cache_dir / f"{cache_key}.body"
        meta_path = renderer.resource_cache_dir / f"{cache_key}.meta"
        body_path.write_bytes(b"body { font-family: Test; }")
        meta_path.write_text(
            json.dumps(
                {
                    "url": url,
                    "content_type": "text/css; charset=utf-8",
                    "fetched_at": time.time(),
                }
            ),
            encoding="utf-8",
        )

        with patch.object(renderer, "_fetch_remote_resource_async") as fetch:
            cached = await renderer._get_remote_resource(url, ttl=3600, timeout=1.0)

        assert cached is not None
        assert cached.body == b"body { font-family: Test; }"
        assert cached.content_type == "text/css; charset=utf-8"
        assert cached.cache_state == "cache hit"
        fetch.assert_not_called()

    @pytest.mark.asyncio
    async def test_browser_headers_do_not_reuse_legacy_css_cache(
        self, renderer: ImageRenderer
    ) -> None:
        """A negotiated browser response must not reuse old non-browser CSS."""
        url = "https://fonts.googleapis.cn/css2?family=Noto+Sans+SC"
        legacy_cache_key = _resource_cache_key(url)
        (renderer.resource_cache_dir / f"{legacy_cache_key}.body").write_bytes(
            b"src: url(legacy-font.ttf)"
        )
        (renderer.resource_cache_dir / f"{legacy_cache_key}.meta").write_text(
            json.dumps(
                {
                    "url": url,
                    "content_type": "text/css",
                    "fetched_at": time.time(),
                }
            ),
            encoding="utf-8",
        )

        with patch.object(
            renderer,
            "_fetch_remote_resource_async",
            return_value=("text/css", b"src: url(modern-font.woff2)"),
        ) as fetch:
            cached = await renderer._get_remote_resource(
                url,
                ttl=3600,
                timeout=1.0,
                request_headers={
                    "user-agent": "Mozilla/5.0 HeadlessChrome/140.0.0.0",
                    "accept": "text/css,*/*;q=0.1",
                },
            )

        assert cached is not None
        assert b"modern-font.woff2" in cached.body
        fetch.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_get_remote_resource_uses_stale_cache_on_fetch_failure(
        self, renderer: ImageRenderer
    ) -> None:
        """Test stale render resources are used when refresh fails."""
        url = "https://fonts.gstatic.cn/s/font.woff2"
        cache_key = _resource_cache_key(url)
        body_path = renderer.resource_cache_dir / f"{cache_key}.body"
        meta_path = renderer.resource_cache_dir / f"{cache_key}.meta"
        body_path.write_bytes(b"font-bytes")
        meta_path.write_text(
            json.dumps(
                {
                    "url": url,
                    "content_type": "font/woff2",
                    "fetched_at": time.time() - 7200,
                }
            ),
            encoding="utf-8",
        )

        with patch.object(
            renderer,
            "_fetch_remote_resource_async",
            side_effect=TimeoutError("timeout"),
        ):
            cached = await renderer._get_remote_resource(url, ttl=3600, timeout=1.0)

        assert cached is not None
        assert cached.body == b"font-bytes"
        assert cached.content_type == "font/woff2"
        assert cached.cache_state == "stale cache"

    @pytest.mark.asyncio
    async def test_get_remote_resource_uses_stale_cache_when_refresh_is_oversized(
        self, renderer: ImageRenderer
    ) -> None:
        """Test an oversized refresh cannot displace an existing stale font."""
        url = "https://fonts.gstatic.cn/s/font.woff2"
        cache_key = _resource_cache_key(url)
        body_path = renderer.resource_cache_dir / f"{cache_key}.body"
        meta_path = renderer.resource_cache_dir / f"{cache_key}.meta"
        body_path.write_bytes(b"stale-font-bytes")
        meta_path.write_text(
            json.dumps(
                {
                    "url": url,
                    "content_type": "font/woff2",
                    "fetched_at": time.time() - 7200,
                }
            ),
            encoding="utf-8",
        )

        with patch.object(
            renderer,
            "_fetch_remote_resource_async",
            side_effect=ValueError("Remote resource too large"),
        ):
            cached = await renderer._get_remote_resource(url, ttl=3600, timeout=1.0)

        assert cached is not None
        assert cached.body == b"stale-font-bytes"
        assert cached.content_type == "font/woff2"
        assert cached.cache_state == "stale cache"

    @pytest.mark.asyncio
    async def test_install_resource_cache_routes_disabled(
        self, renderer: ImageRenderer
    ) -> None:
        """Test fonts remain abortable while other requests bypass a disabled cache."""
        renderer.render_config.remote_resource_cache_enabled = False
        page = AsyncMock()

        fonts = await renderer._install_resource_cache_routes(page)

        page.route.assert_awaited_once()
        handle_route = page.route.call_args.args[1]
        stylesheet = AsyncMock()
        stylesheet.request = MagicMock(
            url="https://fonts.googleapis.com/style.css", resource_type="stylesheet"
        )
        stylesheet_response = MagicMock(status=200, headers={})
        stylesheet.fetch.return_value = stylesheet_response
        font = AsyncMock()
        font.request = MagicMock(
            url="https://fonts.gstatic.com/font.woff2", resource_type="font"
        )
        response = MagicMock()
        font.fetch.return_value = response

        with patch.object(renderer, "_get_remote_resource") as get_resource:
            await handle_route(stylesheet)
            await handle_route(font)
            await fonts.abort_pending()

        stylesheet.continue_.assert_not_awaited()
        stylesheet.fetch.assert_awaited_once_with(
            url="https://fonts.googleapis.com/style.css",
            max_redirects=0,
            timeout=10000,
        )
        stylesheet.fulfill.assert_awaited_once_with(response=stylesheet_response)
        font.fetch.assert_awaited_once_with(
            url="https://fonts.gstatic.com/font.woff2",
            max_redirects=0,
            timeout=10000,
        )
        font.fulfill.assert_awaited_once_with(response=response)
        font.abort.assert_not_awaited()
        get_resource.assert_not_awaited()
        assert not fonts.pending

    @pytest.mark.asyncio
    async def test_resource_route_allows_configured_host_and_subdomains(
        self, renderer: ImageRenderer
    ) -> None:
        """Configured hosts and their subdomains remain available to Chromium."""
        renderer.render_config.remote_resource_cache_enabled = False
        renderer.render_config.allowed_resource_hosts = ["assets.example"]
        page = AsyncMock()
        await renderer._install_resource_cache_routes(page)
        handle_route = page.route.call_args.args[1]

        for url in (
            "https://assets.example/font.woff2",
            "https://cdn.assets.example/font.woff2",
        ):
            route = AsyncMock()
            route.request = MagicMock(url=url, resource_type="font")
            response = MagicMock()
            route.fetch.return_value = response

            await handle_route(route)

            route.fetch.assert_awaited_once_with(
                url=url, max_redirects=0, timeout=10000
            )
            route.fulfill.assert_awaited_once_with(response=response)
            route.abort.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "url",
        [
            "https://evil.example/x.png?secret=hidden",
            "http://169.254.169.254/latest/meta-data/",
            "file:///etc/passwd",
            "ftp://fonts.gstatic.com/font.woff2",
        ],
    )
    async def test_resource_route_aborts_disallowed_urls_once(
        self,
        renderer: ImageRenderer,
        url: str,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Disallowed hosts and schemes are aborted without fetching or leaking queries."""
        page = AsyncMock()
        await renderer._install_resource_cache_routes(page)
        handle_route = page.route.call_args.args[1]
        first_route = AsyncMock()
        first_route.request = MagicMock(url=url, resource_type="image")
        repeated_route = AsyncMock()
        repeated_route.request = MagicMock(url=url, resource_type="image")

        with caplog.at_level(logging.WARNING):
            await handle_route(first_route)
            await handle_route(repeated_route)

        first_route.abort.assert_awaited_once()
        repeated_route.abort.assert_awaited_once()
        first_route.fetch.assert_not_awaited()
        first_route.continue_.assert_not_awaited()
        assert caplog.text.count("Blocked render resource URL:") == 1
        assert "secret=hidden" not in caplog.text

    @pytest.mark.asyncio
    @pytest.mark.parametrize("url", ["data:image/png;base64,AA==", "blob:null/id"])
    async def test_resource_route_preserves_inline_schemes(
        self, renderer: ImageRenderer, url: str
    ) -> None:
        """Inline data and browser-managed blob URLs remain usable if routed."""
        page = AsyncMock()
        await renderer._install_resource_cache_routes(page)
        route = AsyncMock()
        route.request = MagicMock(url=url, resource_type="image")

        await page.route.call_args.args[1](route)

        route.continue_.assert_awaited_once()
        route.abort.assert_not_awaited()
        route.fetch.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_resource_route_allows_bounded_cross_host_redirects(
        self, renderer: ImageRenderer
    ) -> None:
        """Allowed cross-host and HTTP-to-HTTPS redirects are fetched explicitly."""
        renderer.render_config.remote_resource_cache_enabled = False
        page = AsyncMock()
        await renderer._install_resource_cache_routes(page)
        route = AsyncMock()
        route.request = MagicMock(
            url="http://fonts.googleapis.cn/font.css", resource_type="stylesheet"
        )
        redirected = MagicMock(
            status=302,
            headers={"location": "https://fonts.gstatic.cn/font.css"},
        )
        route.fetch.return_value = redirected

        with patch.object(
            renderer,
            "_fetch_remote_resource_async",
            return_value=("text/css", b"body {}"),
        ) as fetch_remote:
            await page.route.call_args.args[1](route)

        assert [call.kwargs["url"] for call in route.fetch.await_args_list] == [
            "http://fonts.googleapis.cn/font.css",
        ]
        fetch_remote.assert_awaited_once_with(
            "https://fonts.gstatic.cn/font.css",
            renderer.render_config.page_load_timeout_sec,
            request_headers=None,
            blocked_urls=set(),
            max_redirects=4,
        )
        route.fulfill.assert_awaited_once_with(
            status=200,
            content_type="text/css",
            headers={"Cache-Control": "public, max-age=0"},
            body=b"body {}",
        )
        route.abort.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_resource_route_allows_same_scheme_cross_host_redirect(
        self, renderer: ImageRenderer
    ) -> None:
        """Allowed cross-host redirects remain on Playwright when schemes match."""
        renderer.render_config.remote_resource_cache_enabled = False
        page = AsyncMock()
        await renderer._install_resource_cache_routes(page)
        route = AsyncMock()
        route.request = MagicMock(
            url="https://fonts.googleapis.cn/font.css", resource_type="stylesheet"
        )
        route.fetch.side_effect = [
            MagicMock(
                status=302,
                headers={"location": "https://fonts.gstatic.cn/font.css"},
            ),
            MagicMock(status=200, headers={}),
        ]

        await page.route.call_args.args[1](route)

        assert [call.kwargs["url"] for call in route.fetch.await_args_list] == [
            "https://fonts.googleapis.cn/font.css",
            "https://fonts.gstatic.cn/font.css",
        ]
        route.abort.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_resource_route_carries_remaining_budget_across_scheme_change(
        self, renderer: ImageRenderer
    ) -> None:
        """Playwright and HTTPX redirects share one global hop budget."""
        renderer.render_config.remote_resource_cache_enabled = False
        page = AsyncMock()
        await renderer._install_resource_cache_routes(page)
        route = AsyncMock()
        route.request = MagicMock(
            url="http://fonts.googleapis.cn/a.css", resource_type="stylesheet"
        )
        route.fetch.side_effect = [
            MagicMock(status=302, headers={"location": "/b.css"}),
            MagicMock(status=302, headers={"location": "/c.css"}),
            MagicMock(status=302, headers={"location": "/d.css"}),
            MagicMock(
                status=302,
                headers={"location": "https://fonts.gstatic.cn/e.css"},
            ),
        ]

        with patch.object(
            renderer,
            "_fetch_remote_resource_async",
            return_value=("text/css", b"body {}"),
        ) as fetch_remote:
            await page.route.call_args.args[1](route)

        fetch_remote.assert_awaited_once_with(
            "https://fonts.gstatic.cn/e.css",
            renderer.render_config.page_load_timeout_sec,
            request_headers=None,
            blocked_urls=set(),
            max_redirects=1,
        )

    @pytest.mark.asyncio
    async def test_resource_route_does_not_follow_not_modified_location(
        self, renderer: ImageRenderer
    ) -> None:
        """A 304 Location header is a final response, not a redirect hop."""
        renderer.render_config.remote_resource_cache_enabled = False
        page = AsyncMock()
        await renderer._install_resource_cache_routes(page)
        route = AsyncMock()
        route.request = MagicMock(
            url="https://fonts.googleapis.cn/font.css", resource_type="stylesheet"
        )
        response = MagicMock(
            status=304,
            headers={"location": "https://evil.example/font.css"},
        )
        route.fetch.return_value = response

        await page.route.call_args.args[1](route)

        route.fetch.assert_awaited_once()
        route.fulfill.assert_awaited_once_with(response=response)
        route.abort.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_resource_route_blocks_redirect_before_target_fetch(
        self, renderer: ImageRenderer, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A redirect to a disallowed host is aborted before requesting that target."""
        renderer.render_config.remote_resource_cache_enabled = False
        page = AsyncMock()
        await renderer._install_resource_cache_routes(page)
        route = AsyncMock()
        route.request = MagicMock(
            url="https://fonts.googleapis.cn/font.css", resource_type="stylesheet"
        )
        route.fetch.return_value = MagicMock(
            status=302,
            headers={"location": "https://evil.example/font.css?token=hidden"},
        )

        with caplog.at_level(logging.WARNING):
            await page.route.call_args.args[1](route)
            direct_route = AsyncMock()
            direct_route.request = MagicMock(
                url="https://evil.example/font.css?token=hidden",
                resource_type="stylesheet",
            )
            await page.route.call_args.args[1](direct_route)

        route.fetch.assert_awaited_once()
        route.fulfill.assert_not_awaited()
        route.abort.assert_awaited_once()
        direct_route.abort.assert_awaited_once()
        assert "evil.example/font.css" in caplog.text
        assert "token=hidden" not in caplog.text
        assert caplog.text.count("Blocked render resource URL:") == 1

    @pytest.mark.asyncio
    async def test_resource_route_caps_redirect_chain(
        self, renderer: ImageRenderer, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Redirect loops stop at the configured hop limit."""
        renderer.render_config.remote_resource_cache_enabled = False
        renderer.render_config.remote_resource_max_redirects = 2
        page = AsyncMock()
        await renderer._install_resource_cache_routes(page)
        route = AsyncMock()
        route.request = MagicMock(
            url="https://fonts.googleapis.cn/a.css", resource_type="stylesheet"
        )
        route.fetch.side_effect = [
            MagicMock(status=302, headers={"location": "/b.css"}),
            MagicMock(status=302, headers={"location": "/c.css"}),
            MagicMock(status=302, headers={"location": "/d.css"}),
        ]

        with caplog.at_level(logging.WARNING):
            await page.route.call_args.args[1](route)

        assert route.fetch.await_count == 3
        route.abort.assert_awaited_once()
        assert "redirect limit exceeded" in caplog.text

    @pytest.mark.asyncio
    async def test_cached_resource_route_aborts_blocked_redirect(
        self, renderer: ImageRenderer, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A cache refresh redirect to a blocked host aborts instead of falling back."""
        page = AsyncMock()
        await renderer._install_resource_cache_routes(page)
        route = AsyncMock()
        route.request = MagicMock(
            url="https://fonts.googleapis.cn/font.css",
            resource_type="stylesheet",
            headers={},
        )
        redirect = MagicMock()
        redirect.headers = {"location": "https://evil.example/font.css"}
        redirect.is_redirect = True

        with patch("app.services.renderer.create_async_client") as client_cls:
            client = MagicMock()
            response_cm = MagicMock()
            response_cm.__aenter__ = AsyncMock(return_value=redirect)
            response_cm.__aexit__ = AsyncMock(return_value=None)
            client.stream.return_value = response_cm
            client_cls.return_value.__aenter__ = AsyncMock(return_value=client)
            client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            with caplog.at_level(logging.WARNING):
                await page.route.call_args.args[1](route)

        assert client.stream.call_count == 1
        route.abort.assert_awaited_once()
        route.fulfill.assert_not_awaited()
        assert renderer._render_degraded is False
        assert caplog.text.count("Blocked render resource URL:") == 1

    @pytest.mark.asyncio
    async def test_cached_resource_route_aborts_redirect_over_limit(
        self, renderer: ImageRenderer, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A cache refresh redirect loop aborts instead of using stylesheet fallback."""
        renderer.render_config.remote_resource_max_redirects = 2
        page = AsyncMock()
        await renderer._install_resource_cache_routes(page)
        route = AsyncMock()
        route.request = MagicMock(
            url="https://fonts.googleapis.cn/a.css",
            resource_type="stylesheet",
            headers={},
        )
        response_contexts = []
        for location in ("/b.css", "/c.css", "/d.css"):
            response = MagicMock()
            response.headers = {"location": location}
            response.is_redirect = True
            response_cm = MagicMock()
            response_cm.__aenter__ = AsyncMock(return_value=response)
            response_cm.__aexit__ = AsyncMock(return_value=None)
            response_contexts.append(response_cm)

        with patch("app.services.renderer.create_async_client") as client_cls:
            client = MagicMock()
            client.stream.side_effect = response_contexts
            client_cls.return_value.__aenter__ = AsyncMock(return_value=client)
            client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            with caplog.at_level(logging.WARNING):
                await page.route.call_args.args[1](route)

        assert client.stream.call_count == 3
        route.abort.assert_awaited_once()
        route.fulfill.assert_not_awaited()
        assert renderer._render_degraded is False
        assert "redirect limit exceeded" in caplog.text

    @pytest.mark.asyncio
    async def test_uncached_font_fetch_failure_aborts_route(
        self, renderer: ImageRenderer
    ) -> None:
        """Test failed font fetches settle the browser request with fallback fonts."""
        renderer.render_config.remote_resource_cache_enabled = False
        page = AsyncMock()
        fonts = await renderer._install_resource_cache_routes(page)
        route = AsyncMock()
        route.request = MagicMock(
            url="https://fonts.gstatic.com/font.woff2", resource_type="font"
        )
        route.fetch.side_effect = TimeoutError("font fetch timed out")

        await page.route.call_args.args[1](route)
        await fonts.abort_pending()

        route.abort.assert_awaited_once()
        route.fulfill.assert_not_awaited()
        assert not fonts.pending

    def test_rewrite_css_urls_matches_standard_syntax(
        self, renderer: ImageRenderer
    ) -> None:
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
            b'body { src: url("font.woff2") }', base_url, "text/css; charset=utf-8"
        )
        assert "font.woff2" not in result.decode() or "https://" in result.decode()

        # url(data:...) - data URI should NOT be rewritten
        result = renderer._rewrite_css_urls(
            b"body { src: url(data:image/svg+xml;base64,abc) }",
            base_url,
            "text/css; charset=utf-8",
        )
        assert "data:image/svg+xml" in result.decode()

        # url(https://...) - absolute URL should NOT be rewritten
        result = renderer._rewrite_css_urls(
            b"body { src: url(https://example.com/font.woff2) }",
            base_url,
            "text/css; charset=utf-8",
        )
        assert "https://example.com/font.woff2" in result.decode()

    @pytest.mark.asyncio
    async def test_fetch_remote_resource_rejects_oversized_response(
        self, renderer: ImageRenderer
    ) -> None:
        """Test oversized remote resource responses are rejected via content-length."""
        url = "https://fonts.googleapis.cn/css2?family=Test"

        # Test content-length exceeding limit
        mock_response = MagicMock()
        mock_response.headers = {
            "content-type": "text/css",
            "content-length": str(10 * 1024 * 1024),
        }
        mock_response.raise_for_status = MagicMock()
        mock_response.aiter_bytes = MagicMock(return_value=iter([]))

        with patch("app.services.renderer.create_async_client") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.stream = MagicMock()

            # Mock stream context manager
            stream_cm = MagicMock()
            stream_cm.__aenter__ = AsyncMock(return_value=mock_response)
            stream_cm.__aexit__ = AsyncMock(return_value=None)
            mock_client.stream.return_value = stream_cm

            mock_client_cls.return_value.__aenter__ = AsyncMock(
                return_value=mock_client
            )
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            with pytest.raises(ValueError, match="too large"):
                await renderer._fetch_remote_resource_async(url, timeout=5.0)

    @pytest.mark.asyncio
    async def test_fetch_remote_resource_reads_async_stream(
        self, renderer: ImageRenderer
    ) -> None:
        """Test remote resources are read from httpx async byte streams."""
        url = "https://fonts.googleapis.cn/css2?family=Test"

        async def chunks() -> AsyncIterator[bytes]:
            yield b"body { "
            yield b"font-family: Test; }"

        renderer._proxy_url = "http://proxy.example:8080"

        mock_response = MagicMock()
        mock_response.headers = {"content-type": "text/css"}
        mock_response.raise_for_status = MagicMock()
        mock_response.aiter_bytes = MagicMock(return_value=chunks())

        with patch("app.services.renderer.create_async_client") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.stream = MagicMock()

            stream_cm = MagicMock()
            stream_cm.__aenter__ = AsyncMock(return_value=mock_response)
            stream_cm.__aexit__ = AsyncMock(return_value=None)
            mock_client.stream.return_value = stream_cm

            mock_client_cls.return_value.__aenter__ = AsyncMock(
                return_value=mock_client
            )
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            content_type, body = await renderer._fetch_remote_resource_async(
                url, timeout=5.0
            )

        assert content_type == "text/css"
        mock_client_cls.assert_called_once_with(
            timeout=httpx.Timeout(5.0),
            follow_redirects=False,
            proxy_url=renderer._proxy_url,
        )
        assert body == b"body { font-family: Test; }"

    @pytest.mark.asyncio
    async def test_fetch_remote_resource_allows_cross_host_redirect(
        self, renderer: ImageRenderer
    ) -> None:
        """HTTPX cache refresh validates and follows allowed redirect targets."""
        redirect = MagicMock()
        redirect.headers = {"location": "https://fonts.gstatic.cn/font.css"}
        redirect.is_redirect = True
        final = MagicMock()
        final.headers = {"content-type": "text/css"}
        final.is_redirect = False
        final.raise_for_status = MagicMock()

        async def chunks() -> AsyncIterator[bytes]:
            yield b"body {}"

        final.aiter_bytes = MagicMock(return_value=chunks())

        with patch("app.services.renderer.create_async_client") as client_cls:
            client = MagicMock()
            redirect_cm = MagicMock()
            redirect_cm.__aenter__ = AsyncMock(return_value=redirect)
            redirect_cm.__aexit__ = AsyncMock(return_value=None)
            final_cm = MagicMock()
            final_cm.__aenter__ = AsyncMock(return_value=final)
            final_cm.__aexit__ = AsyncMock(return_value=None)
            client.stream.side_effect = [redirect_cm, final_cm]
            client_cls.return_value.__aenter__ = AsyncMock(return_value=client)
            client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            content_type, body = await renderer._fetch_remote_resource_async(
                "http://fonts.googleapis.cn/font.css", timeout=5.0
            )

        assert [call.args[1] for call in client.stream.call_args_list] == [
            "http://fonts.googleapis.cn/font.css",
            "https://fonts.gstatic.cn/font.css",
        ]
        assert content_type == "text/css"
        assert body == b"body {}"

    @pytest.mark.asyncio
    async def test_fetch_remote_resource_blocks_redirect_target(
        self, renderer: ImageRenderer, caplog: pytest.LogCaptureFixture
    ) -> None:
        """HTTPX cache refresh never requests a disallowed redirect target."""
        redirect = MagicMock()
        redirect.headers = {"location": "https://evil.example/font.css?key=hidden"}
        redirect.is_redirect = True

        with patch("app.services.renderer.create_async_client") as client_cls:
            client = MagicMock()
            redirect_cm = MagicMock()
            redirect_cm.__aenter__ = AsyncMock(return_value=redirect)
            redirect_cm.__aexit__ = AsyncMock(return_value=None)
            client.stream.return_value = redirect_cm
            client_cls.return_value.__aenter__ = AsyncMock(return_value=client)
            client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            with caplog.at_level(logging.WARNING):
                with pytest.raises(RenderError, match="redirect target blocked"):
                    await renderer._get_remote_resource(
                        "https://fonts.googleapis.cn/font.css",
                        ttl=0,
                        timeout=5.0,
                        blocked_urls=set(),
                    )

        assert client.stream.call_count == 1
        assert "evil.example/font.css" in caplog.text
        assert "key=hidden" not in caplog.text

    @pytest.mark.asyncio
    async def test_fetch_remote_resource_rejects_disallowed_initial_url(
        self, renderer: ImageRenderer, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The HTTPX helper rejects disallowed initial URLs before opening a client."""
        with patch("app.services.renderer.create_async_client") as client_cls:
            with caplog.at_level(logging.WARNING):
                with pytest.raises(RenderError, match="not allowed"):
                    await renderer._fetch_remote_resource_async(
                        "https://evil.example/font.css",
                        timeout=5.0,
                        blocked_urls=set(),
                    )

        client_cls.assert_not_called()
        assert caplog.text.count("Blocked render resource URL:") == 1

    @pytest.mark.asyncio
    async def test_fetch_remote_resource_caps_redirect_chain(
        self, renderer: ImageRenderer, caplog: pytest.LogCaptureFixture
    ) -> None:
        """HTTPX cache refresh stops redirect loops at the configured limit."""
        renderer.render_config.remote_resource_max_redirects = 2
        redirects = []
        for location in ("/b.css", "/c.css", "/d.css"):
            response = MagicMock()
            response.headers = {"location": location}
            response.is_redirect = True
            response_cm = MagicMock()
            response_cm.__aenter__ = AsyncMock(return_value=response)
            response_cm.__aexit__ = AsyncMock(return_value=None)
            redirects.append(response_cm)

        with patch("app.services.renderer.create_async_client") as client_cls:
            client = MagicMock()
            client.stream.side_effect = redirects
            client_cls.return_value.__aenter__ = AsyncMock(return_value=client)
            client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            with caplog.at_level(logging.WARNING):
                with pytest.raises(RenderError, match="redirect limit exceeded"):
                    await renderer._get_remote_resource(
                        "https://fonts.googleapis.cn/a.css", ttl=0, timeout=5.0
                    )

        assert client.stream.call_count == 3
        assert "redirect limit exceeded" in caplog.text

    @pytest.mark.asyncio
    async def test_resource_route_forwards_browser_content_negotiation_headers(
        self, renderer: ImageRenderer
    ) -> None:
        """Google Fonts must see Chromium headers so it returns modern font formats."""
        page = AsyncMock()
        await renderer._install_resource_cache_routes(page)

        route = AsyncMock()
        request = MagicMock()
        request.url = "https://fonts.googleapis.cn/css2?family=Noto+Sans+SC"
        request.resource_type = "stylesheet"
        request.headers = {
            "user-agent": "Mozilla/5.0 HeadlessChrome/140.0.0.0",
            "accept": "text/css,*/*;q=0.1",
            "cookie": "must-not-be-forwarded=1",
        }
        route.request = request

        with patch.object(
            renderer, "_get_remote_resource", return_value=None
        ) as get_resource:
            handle_route = page.route.call_args[0][1]
            await handle_route(route)

        get_resource.assert_awaited_once_with(
            request.url,
            ttl=renderer.render_config.remote_resource_cache_ttl_sec,
            timeout=renderer.render_config.remote_resource_timeout_sec,
            request_headers={
                "user-agent": "Mozilla/5.0 HeadlessChrome/140.0.0.0",
                "accept": "text/css,*/*;q=0.1",
            },
            blocked_urls=set(),
        )

    @pytest.mark.asyncio
    async def test_fetch_remote_resource_uses_browser_headers(
        self, renderer: ImageRenderer
    ) -> None:
        """Test the remote fetch preserves the selected browser headers."""
        url = "https://fonts.googleapis.cn/css2?family=Noto+Sans+SC"
        request_headers = {
            "user-agent": "Mozilla/5.0 HeadlessChrome/140.0.0.0",
            "accept": "text/css,*/*;q=0.1",
        }

        async def chunks() -> AsyncIterator[bytes]:
            yield b"@font-face {}"

        mock_response = MagicMock()
        mock_response.headers = {"content-type": "text/css"}
        mock_response.raise_for_status = MagicMock()
        mock_response.aiter_bytes = MagicMock(return_value=chunks())

        with patch("app.services.renderer.create_async_client") as mock_client_cls:
            mock_client = AsyncMock()
            stream_cm = MagicMock()
            stream_cm.__aenter__ = AsyncMock(return_value=mock_response)
            stream_cm.__aexit__ = AsyncMock(return_value=None)
            mock_client.stream = MagicMock(return_value=stream_cm)
            mock_client_cls.return_value.__aenter__ = AsyncMock(
                return_value=mock_client
            )
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            await renderer._fetch_remote_resource_async(
                url, timeout=5.0, request_headers=request_headers
            )

        mock_client.stream.assert_called_once_with("GET", url, headers=request_headers)

    @pytest.mark.asyncio
    async def test_install_resource_cache_routes_adds_cors_for_cached_fonts(
        self, renderer: ImageRenderer
    ) -> None:
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

        renderer._write_cached_resource(
            body_path, meta_path, url, content_type, body, fetched_at
        )

        assert body_path.exists()
        assert body_path.read_bytes() == body
        assert meta_path.exists()
        meta_data = json.loads(meta_path.read_text(encoding="utf-8"))
        assert meta_data["url"] == url
        assert meta_data["content_type"] == content_type
        assert meta_data["fetched_at"] == fetched_at
        assert "stored_at" in meta_data
        assert meta_data["size"] == len(body)

    def test_read_cached_resource_meta_handles_old_format(
        self, renderer: ImageRenderer
    ) -> None:
        """Test old newline-separated meta format is handled gracefully."""
        cache_key = "old_format_test"
        meta_path = renderer.resource_cache_dir / f"{cache_key}.meta"
        # Write old-format (newline-separated) meta file
        meta_path.write_text(
            "https://example.com\ntext/css\n1234567890.0\n", encoding="utf-8"
        )

        result = renderer._read_cached_resource_meta(meta_path)
        assert result is None

    @pytest.mark.asyncio
    async def test_render_degraded_flag_set_on_empty_stylesheet(
        self, renderer: ImageRenderer
    ) -> None:
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
