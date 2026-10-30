"""app/services/renderer.py 图片渲染服务测试。"""

import asyncio
import hashlib
import json
import logging
import os
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
    """format_datetime 函数测试。"""

    def test_new_format_string(self) -> None:
        """测试新格式日期字符串（YYYY/MM/DD HH:MM:SS）。"""
        result = format_datetime("2026/02/04 10:30:00")
        assert result == "2026-02-04 10:30"

    def test_rfc3339_string(self) -> None:
        """测试 RFC3339 字符串格式化。"""
        result = format_datetime("2026-02-04T10:30:00+08:00")
        assert result == "2026-02-04 10:30"

    def test_rfc3339_with_z_suffix(self) -> None:
        """测试带 Z 后缀的 RFC3339 字符串。"""
        result = format_datetime("2026-02-04T02:30:00Z")
        assert result == "2026-02-04 02:30"

    def test_datetime_object(self) -> None:
        """测试 datetime 对象格式化。"""
        dt = datetime(2026, 2, 4, 10, 30, 0)
        result = format_datetime(dt)
        assert result == "2026-02-04 10:30"

    def test_unix_timestamp_int(self) -> None:
        """测试整数 Unix 时间戳格式化。"""
        # 2026-02-04 10:30:00 UTC+8 = 1738635000
        timestamp = 1738635000
        result = format_datetime(timestamp)
        # 结果取决于本地时区，仅检查格式
        assert len(result) == 16  # 格式为 "YYYY-MM-DD HH:MM"

    def test_unix_timestamp_float(self) -> None:
        """测试浮点数 Unix 时间戳格式化。"""
        timestamp = 1738635000.5
        result = format_datetime(timestamp)
        assert len(result) == 16

    def test_none_returns_placeholder(self) -> None:
        """测试 None 返回占位符。"""
        result = format_datetime(None)
        assert result == "--"

    def test_empty_string_returns_placeholder(self) -> None:
        """测试空字符串返回占位符。"""
        result = format_datetime("")
        assert result == "--"

    def test_whitespace_string_returns_placeholder(self) -> None:
        """测试纯空白字符串返回占位符。"""
        result = format_datetime("   ")
        assert result == "--"

    def test_invalid_string_returns_placeholder(self) -> None:
        """测试无效字符串返回占位符。"""
        result = format_datetime("not a date")
        assert result == "--"

    def test_bool_returns_placeholder(self) -> None:
        """测试 bool（int 的子类）返回占位符。"""
        result = format_datetime(True)
        assert result == "--"

    def test_invalid_timestamp_returns_placeholder(self) -> None:
        """测试无效时间戳返回占位符。"""
        result = format_datetime(99999999999999)  # 数值过大
        assert result == "--"


class TestNl2br:
    """nl2br 函数测试。"""

    def test_converts_newlines(self) -> None:
        """测试将换行符转换为 br 标签。"""
        result = nl2br("Line1\nLine2")
        assert "<br>" in str(result)
        assert isinstance(result, Markup)

    def test_escapes_html(self) -> None:
        """测试转义 HTML 特殊字符。"""
        result = nl2br("<script>alert('xss')</script>")
        assert "<script>" not in str(result)
        assert "&lt;script&gt;" in str(result)

    def test_none_returns_empty(self) -> None:
        """测试 None 返回空 Markup。"""
        result = nl2br(None)
        assert str(result) == ""

    def test_empty_string_returns_empty(self) -> None:
        """测试空字符串返回空 Markup。"""
        result = nl2br("")
        assert str(result) == ""


class TestImageRenderer:
    """ImageRenderer 类测试。"""

    def test_filename_defaults_to_business_time(self, renderer: ImageRenderer) -> None:
        """测试 UTC 仍为前一天时，文件名使用业务日期。"""
        with (
            freeze_time("2026-02-04T18:00:00+00:00"),
            patch("app.services.calendar._business_timezone", ZoneInfo("Asia/Shanghai")),
        ):
            assert renderer._generate_filename("test") == "test_20260205_020000.jpg"

    @pytest.mark.asyncio
    async def test_render_filename_uses_snapshot(self, renderer: ImageRenderer) -> None:
        """测试渲染文件名保留传入的时间戳。"""
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

    @pytest.mark.asyncio
    async def test_render_bytes_uses_screenshot_path_without_writing(
        self,
        templates_config: TemplatesConfig,
        render_config: TemplateRenderConfig,
        tmp_path: Path,
        logger,
    ) -> None:
        """预览渲染返回 JPEG 字节，不创建输出目录。"""
        images_dir = tmp_path / "missing" / "images"
        renderer = ImageRenderer(
            templates_config=templates_config,
            images_dir=str(images_dir),
            render_config=render_config,
            logger=logger,
        )
        page = AsyncMock()
        page.on = MagicMock()
        page.evaluate.side_effect = ["ready", 600]
        page.screenshot.return_value = b"preview jpeg"

        with patch("app.services.renderer.browser_manager") as manager:
            manager.create_page = AsyncMock(return_value=page)
            manager.release_page = AsyncMock()
            result = await renderer.render_bytes({"title": "Preview"}, "test")

        assert result == b"preview jpeg"
        assert not images_dir.exists()
        assert list(renderer.resource_cache_dir.iterdir()) == []
        page.set_content.assert_awaited_once_with(
            "<html><body>Preview</body></html>",
            wait_until="domcontentloaded",
            timeout=10000,
        )
        page.screenshot.assert_awaited_once_with(
            type="jpeg", quality=90, full_page=True, timeout=10000
        )
        manager.release_page.assert_awaited_once_with(page)

    @pytest.fixture
    def templates_config(self, tmp_path: Path) -> TemplatesConfig:
        """创建模板配置。"""
        # 创建简单的模板文件
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
        """创建渲染配置。"""
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
        """创建 ImageRenderer 实例。"""
        images_dir = tmp_path / "static"
        images_dir.mkdir()
        return ImageRenderer(
            templates_config=templates_config,
            images_dir=str(images_dir),
            render_config=render_config,
            logger=logger,
        )

    @pytest.mark.asyncio
    async def test_render_creates_images_dir(
        self,
        templates_config: TemplatesConfig,
        render_config: TemplateRenderConfig,
        tmp_path: Path,
        logger,
    ) -> None:
        """测试渲染器在静态目录不存在时创建目录。"""
        images_dir = tmp_path / "new_static"
        renderer = ImageRenderer(
            templates_config=templates_config,
            images_dir=str(images_dir),
            render_config=render_config,
            logger=logger,
        )
        with patch.object(
            renderer, "_generate_screenshot", new=AsyncMock(return_value=b"jpeg")
        ):
            await renderer.render({"title": "test"})

        assert images_dir.exists()

    def test_get_jinja_env(
        self, renderer: ImageRenderer, templates_config: TemplatesConfig
    ) -> None:
        """测试 _get_jinja_env 返回 Environment。"""
        template_item = templates_config.get_template("test")
        env = renderer._get_jinja_env(template_item.path)

        assert env is not None
        # 检查自定义过滤器已注册
        assert "format_datetime" in env.filters
        assert "nl2br" in env.filters

    def test_get_jinja_env_caches_environment(
        self, renderer: ImageRenderer, templates_config: TemplatesConfig
    ) -> None:
        """测试 _get_jinja_env 缓存 Environment。"""
        template_item = templates_config.get_template("test")

        env1 = renderer._get_jinja_env(template_item.path)
        env2 = renderer._get_jinja_env(template_item.path)

        assert env1 is env2

    def test_jinja_environment_auto_reloads_template_changes(
        self, renderer: ImageRenderer, templates_config: TemplatesConfig
    ) -> None:
        """缓存的 Jinja 环境在模板修改后重新加载其内容。"""
        template_item = templates_config.get_template("test")
        template_path = Path(template_item.path)

        assert renderer._render_template({"title": "First"}, template_item) == (
            "<html><body>First</body></html>"
        )
        previous_mtime = template_path.stat().st_mtime
        template_path.write_text(
            "<html><body>changed {{ title }}</body></html>", encoding="utf-8"
        )
        os.utime(template_path, (previous_mtime + 2, previous_mtime + 2))

        assert renderer._render_template({"title": "Second"}, template_item) == (
            "<html><body>changed Second</body></html>"
        )

    @pytest.mark.asyncio
    async def test_fit_viewport_shrinks_to_content(
        self, renderer: ImageRenderer
    ) -> None:
        """内容短于声明的视口时，移除底部空白区域。"""
        page = AsyncMock()
        page.evaluate.side_effect = [420, 420]
        viewport = ViewportConfig(width=800, height=600)

        use_full_page = await renderer._fit_viewport_to_content(page, viewport, "test")

        assert use_full_page is True
        assert page.evaluate.await_count == 2
        page.set_viewport_size.assert_awaited_once_with({"width": 800, "height": 420})

    @pytest.mark.asyncio
    async def test_fit_viewport_grows_and_uses_remeasured_height(
        self, renderer: ImageRenderer
    ) -> None:
        """内容增长和布局重排均影响最终高度。"""
        page = AsyncMock()
        page.evaluate.side_effect = [900, 940]
        viewport = ViewportConfig(width=800, height=600)

        use_full_page = await renderer._fit_viewport_to_content(page, viewport, "test")

        assert use_full_page is True
        assert page.set_viewport_size.await_args_list == [
            (({"width": 800, "height": 900},), {}),
            (({"width": 800, "height": 940},), {}),
        ]

    @pytest.mark.asyncio
    async def test_fit_viewport_clips_content_at_maximum(
        self, renderer: ImageRenderer
    ) -> None:
        """内容过高时使用最大视口高度，并禁用整页截图。"""
        page = AsyncMock()
        page.evaluate.side_effect = [5000, 5000]
        viewport = ViewportConfig(width=800, height=600)

        use_full_page = await renderer._fit_viewport_to_content(page, viewport, "test")

        assert use_full_page is False
        page.set_viewport_size.assert_awaited_once_with(
            {"width": 800, "height": renderer.MAX_VIEWPORT_HEIGHT}
        )

    @pytest.mark.asyncio
    async def test_fit_viewport_keeps_declared_height_for_zero_measurement(
        self, renderer: ImageRenderer
    ) -> None:
        """测量为空或失败时，保留配置的视口高度。"""
        page = AsyncMock()
        page.evaluate.return_value = 0
        viewport = ViewportConfig(width=800, height=600)

        use_full_page = await renderer._fit_viewport_to_content(page, viewport, "test")

        assert use_full_page is True
        page.set_viewport_size.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_render_success(self, renderer: ImageRenderer) -> None:
        """测试渲染成功。"""
        template_data = {"title": "Test Title"}

        # mock 浏览器管理器
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
        # 模板名为 "test"，因此文件名以 "test_" 开头
        assert "test_" in filename
        mock_page.route.assert_awaited_once()
        mock_page.set_content.assert_awaited_once_with(
            "<html>rendered</html>",
            wait_until="domcontentloaded",
            timeout=10000,
        )

    @pytest.mark.asyncio
    async def test_render_with_template_name(self, renderer: ImageRenderer) -> None:
        """测试使用指定模板名称渲染。"""
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
        """测试渲染就绪等待超时后降级，而非导致渲染失败。"""
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
        """测试就绪状态能指示何时需要中止尚未完成的字体请求。"""
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
        """测试就绪等待超时后，先取消字体获取，再使用回退字体截图。"""
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
        """测试清理时中止请求失败不会覆盖原始渲染错误。"""
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
        """测试未完成渲染资源的日志包含足够的 URL 细节以诊断超时。"""
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
        """测试 Playwright 资源请求失败时记录资源 URL。"""
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
        """测试远程字体资源被选中缓存。"""
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
        """测试未过期的渲染资源缓存无需重新获取即可使用。"""
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
        """浏览器内容协商响应不得复用旧的非浏览器 CSS 缓存。"""
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
        """测试刷新失败时使用过期的渲染资源缓存。"""
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
        """测试刷新响应过大时不会替换已有的过期字体缓存。"""
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
        """测试禁用缓存后其他请求绕过缓存，字体请求仍可中止。"""
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
        """Chromium 仍可访问配置的主机及其子域名。"""
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
        """中止不允许的主机和协议请求，不发起获取，也不泄露查询参数。"""
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
        """内联 data 和浏览器管理的 blob URL 在被路由拦截时仍可用。"""
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
        """显式获取允许的跨主机及 HTTP 到 HTTPS 重定向目标。"""
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
        """协议相同时，允许的跨主机重定向仍由 Playwright 处理。"""
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
        """Playwright 和 HTTPX 重定向共用一个全局跳转次数上限。"""
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
        """带 Location 头的 304 响应是最终响应，不算重定向跳转。"""
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
        """重定向至不允许的主机时，在请求目标前中止。"""
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
        """重定向循环在达到配置的跳转次数上限时停止。"""
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
        """缓存刷新重定向到被拦截的主机时中止，而非回退。"""
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
        """缓存刷新遇到重定向循环时中止，而非使用样式表回退。"""
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
        """测试字体获取失败时结束浏览器请求，改用回退字体。"""
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
        """测试 CSS URL 重写正则正确处理标准 CSS 语法。"""
        base_url = "https://fonts.googleapis.cn/css2?family=Test"

        # url(path)：应重写无引号的相对路径
        result = renderer._rewrite_css_urls(
            b"body { src: url(font.woff2) }", base_url, "text/css; charset=utf-8"
        )
        assert "url(https://fonts.googleapis.cn/font.woff2)" in result.decode()

        # url('path')：应重写单引号包裹的相对路径
        result = renderer._rewrite_css_urls(
            b"body { src: url('font.woff2') }", base_url, "text/css; charset=utf-8"
        )
        assert "url('https://fonts.googleapis.cn/font.woff2')" in result.decode()

        # url("path")：应重写双引号包裹的相对路径
        result = renderer._rewrite_css_urls(
            b'body { src: url("font.woff2") }', base_url, "text/css; charset=utf-8"
        )
        assert "font.woff2" not in result.decode() or "https://" in result.decode()

        # url(data:...)：不应重写 data URI
        result = renderer._rewrite_css_urls(
            b"body { src: url(data:image/svg+xml;base64,abc) }",
            base_url,
            "text/css; charset=utf-8",
        )
        assert "data:image/svg+xml" in result.decode()

        # url(https://...)：不应重写绝对 URL
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
        """测试根据 content-length 拒绝过大的远程资源响应。"""
        url = "https://fonts.googleapis.cn/css2?family=Test"

        # 测试 content-length 超出限制的情况
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

            # mock 流式上下文管理器
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
        """测试从 httpx 异步字节流读取远程资源。"""
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
        """HTTPX 缓存刷新会校验并跟随允许的重定向目标。"""
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
        """HTTPX 缓存刷新绝不请求不允许的重定向目标。"""
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
        """HTTPX 辅助方法在创建客户端前拒绝不允许的初始 URL。"""
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
        """HTTPX 缓存刷新在达到配置上限时停止重定向循环。"""
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
        """Google Fonts 必须收到 Chromium 请求头才能返回现代字体格式。"""
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
        """测试远程获取保留选定的浏览器请求头。"""
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
        """测试缓存字体响应包含 Chromium 所需的 CORS 头。"""
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
        """测试缓存资源以原子方式写入，并使用 JSON 元数据。"""
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
        """测试妥善处理以换行分隔的旧版元数据格式。"""
        cache_key = "old_format_test"
        meta_path = renderer.resource_cache_dir / f"{cache_key}.meta"
        # 写入旧格式（以换行分隔）的元数据文件
        meta_path.write_text(
            "https://example.com\ntext/css\n1234567890.0\n", encoding="utf-8"
        )

        result = renderer._read_cached_resource_meta(meta_path)
        assert result is None

    @pytest.mark.asyncio
    async def test_render_degraded_flag_set_on_empty_stylesheet(
        self, renderer: ImageRenderer
    ) -> None:
        """测试使用空样式表回退时设置 _render_degraded。"""
        renderer._render_degraded = False

        with patch.object(renderer, "_get_remote_resource", return_value=None):
            page = AsyncMock()
            await renderer._install_resource_cache_routes(page)

            # 模拟样式表请求触发回退
            route = AsyncMock()
            request = MagicMock()
            request.url = "https://fonts.googleapis.cn/css2?family=Test"
            request.resource_type = "stylesheet"
            route.request = request

            # 从 page.route 调用中获取 handle_route 回调
            handle_route = page.route.call_args[0][1]
            await handle_route(route)

        assert renderer._render_degraded is True

    def test_sanitize_url_for_log(self, renderer: ImageRenderer) -> None:
        """测试日志 URL 脱敏时移除查询参数。"""
        url = "https://fonts.googleapis.cn/css2?family=Test&display=swap"
        sanitized = renderer._sanitize_url_for_log(url)
        assert "family=Test" not in sanitized
        assert "display=swap" not in sanitized
        assert "fonts.googleapis.cn" in sanitized
