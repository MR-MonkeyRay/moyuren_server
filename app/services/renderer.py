"""Image rendering service module."""

import asyncio
import hashlib
import inspect
import json
import logging
import os
import re
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from jinja2 import Environment, FileSystemLoader, TemplateError
from markupsafe import Markup, escape

from app.core.config import (
    TemplateItemConfig,
    TemplateRenderConfig,
    TemplatesConfig,
    ViewportConfig,
)
from app.core.network import create_async_client, safe_exception_for_log
from app.core.errors import RenderError, RenderResourceRedirectError
from app.services.browser import browser_manager
from app.services.calendar import now_business

_CACHEABLE_RESOURCE_TYPES = {"stylesheet", "font"}
_CACHEABLE_HOST_SUFFIXES = (
    "fonts.googleapis.com",
    "fonts.googleapis.cn",
    "fonts.gstatic.com",
    "fonts.gstatic.cn",
)
_CONTENT_NEGOTIATION_HEADERS = ("user-agent", "accept")


@dataclass
class _CachedResource:
    """远程渲染资源的缓存读取结果。

    Attributes:
        body: 资源响应体字节内容。
        content_type: 响应的 MIME 类型。
        cache_state: 缓存命中状态，用于调试和响应头标记。
    """

    body: bytes
    content_type: str
    cache_state: str


@dataclass
class _TrackedResource:
    """Playwright request currently active while rendering a template."""

    url: str
    resource_type: str
    started_at: float


@dataclass
class _FontRequests:
    """Keep font routes abortable until their responses are available."""

    logger: logging.Logger
    stopped: bool = False
    pending: dict[Any, asyncio.Task[Any]] = field(default_factory=dict)

    async def abort_pending(self) -> None:
        """Abort pending fonts and prevent subsequent font fetches."""
        self.stopped = True
        pending = list(self.pending.items())
        self.pending.clear()
        for _, task in pending:
            task.cancel()
        await asyncio.gather(*(task for _, task in pending), return_exceptions=True)
        results = await asyncio.gather(
            *(route.abort() for route, _ in pending), return_exceptions=True
        )
        for result in results:
            if isinstance(result, Exception):
                self.logger.warning("Failed to abort font request: %s", safe_exception_for_log(result))


def format_datetime(value: str | datetime | int | float | None) -> str:
    """Format datetime to friendly display format.

    Supports:
    - New format strings (e.g., "2026/02/01 07:22:32")
    - RFC3339 strings (e.g., "2026-02-01T07:22:32+08:00")
    - datetime objects
    - Unix timestamps (int/float, excluding bool)
    - None or invalid values (returns "--")
    """
    # 处理 None
    if value is None:
        return "--"

    # 处理 datetime 对象
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M")

    # 处理数字（时间戳），排除 bool（bool 是 int 的子类）
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            dt = datetime.fromtimestamp(value)
            return dt.strftime("%Y-%m-%d %H:%M")
        except (ValueError, OSError):
            return "--"

    # 处理字符串
    if isinstance(value, str):
        if not value.strip():
            return "--"
        try:
            # Try new format first (YYYY/MM/DD HH:MM:SS)
            dt = datetime.strptime(value, "%Y/%m/%d %H:%M:%S")
            return dt.strftime("%Y-%m-%d %H:%M")
        except ValueError:
            pass
        try:
            # Handle 'Z' suffix (UTC timezone indicator)
            normalized = value.replace("Z", "+00:00") if value.endswith("Z") else value
            dt = datetime.fromisoformat(normalized)
            return dt.strftime("%Y-%m-%d %H:%M")
        except (ValueError, TypeError):
            return "--"

    # 其他类型
    return "--"


def nl2br(value: str | None) -> Markup:
    """Convert newlines to HTML <br> tags.

    Args:
        value: Text with newlines.

    Returns:
        Markup object with <br> tags (safe for HTML output).
    """
    if not value:
        return Markup("")
    # 先转义 HTML 特殊字符，再将换行转为 <br>
    escaped = escape(str(value))
    return Markup(str(escaped).replace("\n", "<br>\n"))  # nosec B704 - input already escaped


class ImageRenderer:
    """HTML template renderer and screenshot generator using reusable browser."""

    MAX_VIEWPORT_HEIGHT = 4000  # Maximum viewport height to prevent memory/CPU issues

    def __init__(
        self,
        templates_config: TemplatesConfig,
        images_dir: str,
        render_config: TemplateRenderConfig,
        logger: logging.Logger,
        proxy_url: str | None = None,
    ) -> None:
        """Initialize the image renderer.

        Args:
            templates_config: Templates configuration.
            images_dir: Directory where generated images will be saved.
            render_config: Render configuration including viewport and quality settings.
            logger: Logger instance for logging render status.
            proxy_url: Optional outbound proxy URL for remote render resources.
        """
        self.templates_config = templates_config
        self.images_dir = Path(images_dir)
        self.render_config = render_config
        self.logger = logger
        self._env_cache: dict[str, Environment] = {}
        self.resource_cache_dir = self.images_dir.parent / "render_resources"
        self._render_degraded: bool = False
        self._proxy_url = proxy_url

        # Ensure images directory exists
        self.images_dir.mkdir(parents=True, exist_ok=True)
        self.resource_cache_dir.mkdir(parents=True, exist_ok=True)

    def _get_jinja_env(self, template_path: str) -> Environment:
        """Get or create Jinja2 environment for template directory."""
        template_dir = str(Path(template_path).parent)
        if template_dir not in self._env_cache:
            env = Environment(
                loader=FileSystemLoader(template_dir),
                autoescape=True,  # 启用自动转义，防止 XSS
            )
            env.filters["format_datetime"] = format_datetime
            env.filters["nl2br"] = nl2br  # 换行转 <br> 过滤器
            self._env_cache[template_dir] = env
        return self._env_cache[template_dir]

    async def render(
        self,
        data: dict[str, Any],
        template_name: str | None = None,
        *,
        now: datetime | None = None,
    ) -> str:
        """Render HTML template and generate screenshot image.

        Args:
            data: Template context data.
            template_name: Template name to use.
            now: Optional business-time snapshot for the output filename.

        Returns:
            The filename of the generated image (e.g., "moyuren_moyuren_20260127_060001.jpg").

        Raises:
            RenderError: If template rendering or screenshot generation fails.
        """
        if now is None:
            now = now_business()
        self._render_degraded = False
        template_item = self.templates_config.get_template(template_name)
        render_start = time.monotonic()
        self.logger.info(
            "Rendering template '%s' from %s (viewport=%sx%s)",
            template_item.name,
            template_item.path,
            template_item.viewport.width,
            template_item.viewport.height,
        )

        # Step 1: Render HTML with Jinja2
        html_content = self._render_template(data, template_item)
        self.logger.info(
            "Rendered HTML for '%s' (%s bytes)", template_item.name, len(html_content)
        )

        # Step 2: Generate screenshot with Playwright
        viewport = template_item.viewport
        jpeg_quality = template_item.jpeg_quality or self.render_config.jpeg_quality
        device_scale_factor = (
            template_item.device_scale_factor or self.render_config.device_scale_factor
        )
        image_bytes = await self._generate_screenshot(
            html_content,
            viewport,
            jpeg_quality,
            device_scale_factor,
            template_item.name,
        )

        # Step 3: Atomically write to file
        filename = self._generate_filename(template_item.name, now=now)
        self._write_file_atomic(filename, image_bytes)

        self.logger.info(
            "Successfully rendered image: %s (%s bytes, %.1fs)",
            filename,
            len(image_bytes),
            time.monotonic() - render_start,
        )
        if self._render_degraded:
            self.logger.warning("Render completed with degraded stylesheet(s)")
        return filename

    def _render_template(
        self, data: dict[str, Any], template_item: TemplateItemConfig
    ) -> str:
        """Render Jinja2 template with provided data.

        Args:
            data: Template context data.
            template_item: Template item configuration.

        Returns:
            Rendered HTML content.

        Raises:
            RenderError: If template rendering fails.
        """
        try:
            env = self._get_jinja_env(template_item.path)
            template_name = Path(template_item.path).name
            template = env.get_template(template_name)
            render_context = {
                **data,
                "use_china_cdn": self.render_config.use_china_cdn,
                "show_kfc": template_item.show_kfc,
                "show_stock": template_item.show_stock,
                "show_daily_english": template_item.show_daily_english,
            }
            return template.render(**render_context)
        except TemplateError as e:
            self.logger.error("Template rendering failed: %s", e)
            raise RenderError(
                message=f"Failed to render template: {e}",
            ) from e

    async def _generate_screenshot(
        self,
        html_content: str,
        viewport: ViewportConfig,
        jpeg_quality: int,
        device_scale_factor: int,
        template_name: str,
    ) -> bytes:
        """Generate screenshot from HTML using Playwright.

        Args:
            html_content: HTML content to render.
            viewport: Viewport configuration.
            jpeg_quality: JPEG quality for screenshot.
            device_scale_factor: Device scale factor for rendering.

        Returns:
            Screenshot image bytes.

        Raises:
            RenderError: If screenshot generation fails.
        """
        page = None
        font_requests: _FontRequests | None = None
        tracked_resources: dict[int, _TrackedResource] = {}
        try:
            self.logger.info(
                "Creating screenshot page for '%s' (scale=%s, quality=%s)",
                template_name,
                device_scale_factor,
                jpeg_quality,
            )
            page = await browser_manager.create_page(
                {
                    "width": viewport.width,
                    "height": viewport.height,
                    "device_scale_factor": device_scale_factor,
                }
            )
            tracked_resources = self._track_page_requests(page)
            font_requests = await self._install_resource_cache_routes(page)

            # Set HTML content without waiting for network idle. Remote font/CDN requests
            # can keep the network busy long enough to fail otherwise valid renders.
            set_content_start = time.monotonic()
            self.logger.info("Loading HTML into browser for '%s'", template_name)
            page_load_timeout_ms = self._timeout_ms(
                self.render_config.page_load_timeout_sec
            )
            await page.set_content(
                html_content,
                wait_until="domcontentloaded",
                timeout=page_load_timeout_ms,
            )
            ready = await self._wait_for_render_ready(
                page, template_name, tracked_resources
            )
            if not ready:
                await font_requests.abort_pending()
            self.logger.info(
                "Browser content loaded for '%s' (%.1fs)",
                template_name,
                time.monotonic() - set_content_start,
            )

            use_full_page = True
            # Adjust viewport height to match actual content height
            # This ensures full_page=True captures content without extra blank space
            content_height = await page.evaluate(
                "Math.max(document.body.scrollHeight, document.documentElement.scrollHeight)"
            )
            if content_height and content_height != viewport.height:
                original_height = content_height
                content_height = min(content_height, self.MAX_VIEWPORT_HEIGHT)
                if original_height > self.MAX_VIEWPORT_HEIGHT:
                    self.logger.error(
                        "Screenshot truncated: content height %spx exceeds max %spx, output will be clipped",
                        original_height,
                        self.MAX_VIEWPORT_HEIGHT,
                    )
                    use_full_page = False
                self.logger.info(
                    "Adjusting viewport for '%s' from %spx to %spx",
                    template_name,
                    viewport.height,
                    content_height,
                )
                await page.set_viewport_size(
                    {"width": viewport.width, "height": content_height}
                )

            # Take screenshot as JPEG
            screenshot_start = time.monotonic()
            self.logger.info("Capturing screenshot for '%s'", template_name)
            screenshot_bytes = await page.screenshot(
                type="jpeg",
                quality=jpeg_quality,
                full_page=use_full_page,
                timeout=page_load_timeout_ms,
            )
            self.logger.info(
                "Screenshot captured for '%s' (%s bytes, %.1fs)",
                template_name,
                len(screenshot_bytes),
                time.monotonic() - screenshot_start,
            )

            return screenshot_bytes

        except Exception as e:
            self._log_pending_render_resources(template_name, tracked_resources)
            self.logger.error("Screenshot generation failed: %s", e)
            raise RenderError(
                message=f"Failed to generate screenshot: {e}",
            ) from e
        finally:
            if page is not None:
                try:
                    if font_requests is not None:
                        await font_requests.abort_pending()
                finally:
                    await browser_manager.release_page(page)

    def _timeout_ms(self, timeout_sec: float) -> int:
        """Convert seconds to Playwright timeout milliseconds."""
        return max(1, int(timeout_sec * 1000))

    def _track_page_requests(self, page: Any) -> dict[int, _TrackedResource]:
        """Track in-flight page requests so timeout logs can name the resource."""
        tracked_resources: dict[int, _TrackedResource] = {}
        on = getattr(page, "on", None)
        if not callable(on) or inspect.iscoroutinefunction(on):
            return tracked_resources

        def remember_request(request: Any) -> None:
            tracked_resources[id(request)] = _TrackedResource(
                url=str(getattr(request, "url", "")),
                resource_type=str(getattr(request, "resource_type", "unknown")),
                started_at=time.monotonic(),
            )

        def forget_request(request: Any) -> None:
            tracked_resources.pop(id(request), None)

        def log_failed_request(request: Any) -> None:
            tracked = tracked_resources.pop(id(request), None)
            url = tracked.url if tracked else str(getattr(request, "url", ""))
            resource_type = (
                tracked.resource_type
                if tracked
                else str(getattr(request, "resource_type", "unknown"))
            )
            self.logger.warning(
                "Render resource request failed: %s %s",
                resource_type,
                self._resource_url_for_log(url),
            )

        try:
            on("request", remember_request)
            on("requestfinished", forget_request)
            on("requestfailed", log_failed_request)
        except Exception as e:
            self.logger.debug("Render resource request tracking disabled: %s", e)
            tracked_resources.clear()
        return tracked_resources

    def _log_pending_render_resources(
        self,
        template_name: str,
        tracked_resources: dict[int, _TrackedResource],
        limit: int = 8,
    ) -> None:
        """Log resource requests still pending during a render timeout/failure."""
        if not tracked_resources:
            return

        now = time.monotonic()
        pending = sorted(
            tracked_resources.values(),
            key=lambda item: item.started_at,
        )
        summary = "; ".join(
            f"{item.resource_type} {self._resource_url_for_log(item.url)} "
            f"({now - item.started_at:.1f}s)"
            for item in pending[:limit]
        )
        remaining = len(pending) - limit
        suffix = f"; +{remaining} more" if remaining > 0 else ""
        self.logger.warning(
            "Pending render resources for %s at timeout/failure: %s%s",
            template_name,
            summary,
            suffix,
        )

    async def _wait_for_render_ready(
        self,
        page: Any,
        template_name: str,
        tracked_resources: dict[int, _TrackedResource] | None = None,
    ) -> bool:
        """Wait briefly for fonts and layout callbacks without blocking rendering."""
        timeout_ms = self._timeout_ms(self.render_config.font_ready_timeout_sec)
        try:
            result = await page.evaluate(
                """async (timeoutMs) => {
                    const settleFrame = () => new Promise(resolve => {
                        requestAnimationFrame(() => requestAnimationFrame(resolve));
                    });
                    if (!document.fonts || !document.fonts.ready) {
                        await settleFrame();
                        return "unsupported";
                    }
                    const timeout = new Promise(resolve => {
                        setTimeout(() => resolve("timeout"), timeoutMs);
                    });
                    const status = await Promise.race([
                        document.fonts.ready.then(() => "ready").catch(() => "error"),
                        timeout,
                    ]);
                    await settleFrame();
                    return status;
                }""",
                timeout_ms,
            )
        except Exception as e:
            self.logger.warning("Render readiness wait skipped for %s: %s", template_name, e)
            if tracked_resources is not None:
                self._log_pending_render_resources(template_name, tracked_resources)
            return False

        if result == "timeout":
            self.logger.warning(
                "Font readiness wait timed out for %s after %.1fs; continuing with fallback fonts",
                template_name,
                self.render_config.font_ready_timeout_sec,
            )
            if tracked_resources is not None:
                self._log_pending_render_resources(template_name, tracked_resources)
        elif result == "error":
            self.logger.warning("Font readiness wait failed for %s; continuing", template_name)
        return result not in ("timeout", "error")

    async def _install_resource_cache_routes(self, page: Any) -> _FontRequests:
        """Keep font requests abortable and optionally cache remote resources."""
        fonts = _FontRequests(logger=self.logger)
        blocked_urls: set[str] = set()
        cache_enabled = self.render_config.remote_resource_cache_enabled
        ttl = self.render_config.remote_resource_cache_ttl_sec
        timeout = self.render_config.remote_resource_timeout_sec
        if cache_enabled:
            self.logger.info(
                "Remote render resource cache enabled (dir=%s, ttl=%ss, timeout=%ss)",
                self.resource_cache_dir,
                ttl,
                timeout,
            )
        else:
            self.logger.info("Remote render resource cache disabled")

        async def handle_route(route: Any) -> None:
            """处理 Playwright 路由并优先使用远程资源缓存。

            Args:
                route: Playwright 路由对象，包含请求信息和响应控制方法。

            Side Effects:
                可能继续原请求、用缓存内容响应请求、返回空样式降级响应或中止请求；
                样式降级时会标记当前渲染为 degraded 并写入日志。
            """
            request = route.request
            url = request.url
            resource_type = getattr(request, "resource_type", "")
            parsed = urlparse(url)
            scheme = parsed.scheme.lower()
            if scheme in {"data", "blob"}:
                await route.continue_()
                return
            if not self._is_allowed_resource_url(url):
                self._log_blocked_resource(url, blocked_urls)
                await route.abort()
                return
            is_font = resource_type == "font"
            if is_font and fonts.stopped:
                await route.abort()
                return
            cacheable = cache_enabled and self._should_cache_resource(url, resource_type)
            async def fetch_resource() -> Any:
                """Fetch through the cache or preserve the browser request settings."""
                if not cacheable:
                    return await self._fetch_route_with_redirects(
                        route,
                        url,
                        blocked_urls,
                    )
                request_headers = self._content_negotiation_headers(request)
                try:
                    return await self._get_remote_resource(
                        url,
                        ttl=ttl,
                        timeout=timeout,
                        request_headers=request_headers,
                        blocked_urls=blocked_urls,
                    )
                except ValueError:
                    # Size limit exceeded - treat as unavailable
                    self.logger.warning(
                        "Render resource rejected (size limit exceeded): %s",
                        self._sanitize_url_for_log(url),
                    )
                    return None

            try:
                if is_font:
                    task = asyncio.create_task(fetch_resource())
                    fonts.pending[route] = task
                    try:
                        cached = await task
                    except asyncio.CancelledError:
                        if not fonts.stopped:
                            raise
                        return
                    finally:
                        fonts.pending.pop(route, None)
                    if fonts.stopped:
                        return
                else:
                    cached = await fetch_resource()
            except RenderResourceRedirectError:
                await route.abort()
                return
            except Exception as e:
                self.logger.warning(
                    "%s resource fetch failed: %s",
                    "Font" if is_font else "Render",
                    safe_exception_for_log(e),
                )
                await route.abort()
                return
            if cached is None and not cacheable:
                await route.abort()
                return
            if not cacheable:
                if isinstance(cached, _CachedResource):
                    await route.fulfill(
                        status=200,
                        content_type=cached.content_type,
                        headers=self._cached_resource_headers(
                            0, resource_type, cached.content_type
                        ),
                        body=cached.body,
                    )
                else:
                    await route.fulfill(response=cached)
                return
            if cached is not None:
                self.logger.info(
                    "Render resource %s: %s", cached.cache_state, self._sanitize_url_for_log(url)
                )
                await route.fulfill(
                    status=200,
                    content_type=cached.content_type,
                    headers=self._cached_resource_headers(
                        ttl, resource_type, cached.content_type
                    ),
                    body=cached.body,
                )
                return

            if resource_type == "stylesheet":
                self.logger.warning(
                    "Using empty stylesheet fallback for render resource: %s",
                    self._sanitize_url_for_log(url),
                )
                self._render_degraded = True
                await route.fulfill(
                    status=200,
                    content_type="text/css; charset=utf-8",
                    headers={"Cache-Control": "no-store"},
                    body=b"",
                )
                return

            self.logger.warning(
                "Aborting uncached render resource after fetch failure: %s",
                self._sanitize_url_for_log(url),
            )
            await route.abort()

        await page.route("**/*", handle_route)
        return fonts

    def _is_allowed_resource_url(self, url: str) -> bool:
        """Return whether an HTTP(S) resource host is allowed during rendering."""
        parsed = urlparse(url)
        if parsed.scheme.lower() not in {"http", "https"}:
            return False
        hostname = (parsed.hostname or "").lower()
        return any(
            hostname == allowed_host or hostname.endswith(f".{allowed_host}")
            for allowed_host in self.render_config.allowed_resource_hosts
        )

    def _log_blocked_resource(self, url: str, blocked_urls: set[str]) -> None:
        """Log a blocked resource URL once for the current rendered page."""
        if url in blocked_urls:
            return
        blocked_urls.add(url)
        self.logger.warning("Blocked render resource URL: %s", self._sanitize_url_for_log(url))

    async def _fetch_route_with_redirects(
        self,
        route: Any,
        url: str,
        blocked_urls: set[str],
    ) -> Any | None:
        """Fetch a Playwright route while validating each redirect target."""
        current_url = url
        timeout_ms = self._timeout_ms(self.render_config.page_load_timeout_sec)
        max_redirects = self.render_config.remote_resource_max_redirects
        for redirect_count in range(max_redirects + 1):
            response = await route.fetch(
                url=current_url,
                max_redirects=0,
                timeout=timeout_ms,
            )
            status = int(getattr(response, "status", 0))
            headers = getattr(response, "headers", {})
            location = headers.get("location") if isinstance(headers, dict) else None
            if status not in {301, 302, 303, 307, 308} or not location:
                return response
            if redirect_count >= max_redirects:
                self.logger.warning(
                    "Render resource redirect limit exceeded: %s",
                    self._sanitize_url_for_log(current_url),
                )
                return None
            next_url = urljoin(current_url, location)
            if not self._is_allowed_resource_url(next_url):
                self._log_blocked_resource(next_url, blocked_urls)
                return None
            if urlparse(next_url).scheme.lower() != urlparse(url).scheme.lower():
                try:
                    content_type, body = await self._fetch_remote_resource_async(
                        next_url,
                        self.render_config.page_load_timeout_sec,
                        request_headers=self._content_negotiation_headers(route.request),
                        blocked_urls=blocked_urls,
                        max_redirects=max_redirects - redirect_count - 1,
                    )
                except RenderResourceRedirectError:
                    return None
                return _CachedResource(
                    body=body,
                    content_type=content_type,
                    cache_state="direct fetch",
                )
            current_url = next_url
        return None

    def _content_negotiation_headers(self, request: Any) -> dict[str, str] | None:
        """Select safe browser headers that affect the CDN response representation."""
        raw_headers = getattr(request, "headers", None)
        if not isinstance(raw_headers, dict):
            return None

        normalized = {
            str(key).lower(): str(value) for key, value in raw_headers.items()
        }
        selected = {
            name: normalized[name]
            for name in _CONTENT_NEGOTIATION_HEADERS
            if normalized.get(name)
        }
        return selected or None

    def _should_cache_resource(self, url: str, resource_type: str) -> bool:
        """判断远程资源是否应进入渲染资源缓存。

        Args:
            url: 资源请求 URL。
            resource_type: Playwright 识别的资源类型。

        Returns:
            当资源类型和主机名都在缓存白名单内时返回 True，否则返回 False。
        """
        if resource_type not in _CACHEABLE_RESOURCE_TYPES:
            return False
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").lower()
        return any(
            hostname == suffix or hostname.endswith(f".{suffix}")
            for suffix in _CACHEABLE_HOST_SUFFIXES
        )

    async def _get_remote_resource(
        self,
        url: str,
        ttl: int,
        timeout: float,
        request_headers: dict[str, str] | None = None,
        blocked_urls: set[str] | None = None,
    ) -> _CachedResource | None:
        """读取或刷新远程渲染资源缓存。

        Args:
            url: 远程资源 URL。
            ttl: 缓存有效期，单位为秒。
            timeout: 远程请求超时时间，单位为秒。
            request_headers: 参与响应表示选择的浏览器请求头。

        Returns:
            可用于响应浏览器请求的缓存资源；无缓存且刷新失败时返回 None。

        Raises:
            ValueError: 当远程资源大小超过配置限制时抛出。

        Side Effects:
            可能发起网络请求、写入缓存文件，并在刷新失败时写入警告日志。
        """
        cache_identity = url
        if request_headers:
            cache_identity = json.dumps(
                {"url": url, "headers": request_headers},
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            )
        cache_key = hashlib.sha256(cache_identity.encode("utf-8")).hexdigest()
        body_path = self.resource_cache_dir / f"{cache_key}.body"
        meta_path = self.resource_cache_dir / f"{cache_key}.meta"
        now = time.time()

        cached_body = self._read_cached_resource_body(body_path)
        cached_meta = self._read_cached_resource_meta(meta_path)
        age = now - cached_meta["fetched_at"] if cached_meta else None
        if cached_body is not None and cached_meta and age is not None and age <= ttl:
            return _CachedResource(
                body=self._rewrite_css_urls(
                    cached_body, url, cached_meta["content_type"]
                ),
                content_type=cached_meta["content_type"],
                cache_state="cache hit",
            )

        try:
            content_type, body = await self._fetch_remote_resource_async(
                url,
                timeout,
                request_headers=request_headers,
                blocked_urls=blocked_urls,
            )
        except ValueError as e:
            if cached_body is not None and cached_meta:
                self.logger.warning(
                    "Render resource refresh exceeded size limit, using stale cache: %s (%s)",
                    self._sanitize_url_for_log(url),
                    safe_exception_for_log(e, url, self._proxy_url),
                )
                return _CachedResource(
                    body=self._rewrite_css_urls(
                        cached_body, url, cached_meta["content_type"]
                    ),
                    content_type=cached_meta["content_type"],
                    cache_state="stale cache",
                )
            raise
        except RenderResourceRedirectError:
            if cached_body is not None and cached_meta:
                return _CachedResource(
                    body=self._rewrite_css_urls(
                        cached_body, url, cached_meta["content_type"]
                    ),
                    content_type=cached_meta["content_type"],
                    cache_state="stale cache",
                )
            raise
        except (httpx.HTTPError, OSError) as e:
            if cached_body is not None and cached_meta:
                self.logger.warning(
                    "Render resource refresh failed, using stale cache: %s (%s)",
                    self._sanitize_url_for_log(url),
                    safe_exception_for_log(e, url, self._proxy_url),
                )
                return _CachedResource(
                    body=self._rewrite_css_urls(
                        cached_body, url, cached_meta["content_type"]
                    ),
                    content_type=cached_meta["content_type"],
                    cache_state="stale cache",
                )
            self.logger.warning(
                "Render resource fetch failed: %s (%s)",
                self._sanitize_url_for_log(url),
                safe_exception_for_log(e, url, self._proxy_url),
            )
            return None

        self._write_cached_resource(body_path, meta_path, url, content_type, body, now)
        return _CachedResource(
            body=self._rewrite_css_urls(body, url, content_type),
            content_type=content_type,
            cache_state="cache refresh",
        )

    async def _fetch_remote_resource_async(
        self,
        url: str,
        timeout: float,
        request_headers: dict[str, str] | None = None,
        blocked_urls: set[str] | None = None,
        max_redirects: int | None = None,
    ) -> tuple[str, bytes]:
        """按流式下载远程资源，并在下载过程中限制资源大小。

        Args:
            url: 远程资源 URL。
            timeout: HTTP 请求超时时间，单位为秒。
            request_headers: 浏览器内容协商头；仅包含安全白名单字段。

        Returns:
            二元组，包含响应 Content-Type 和资源字节内容。

        Raises:
            ValueError: 当 Content-Length 或累计下载大小超过配置限制时抛出。
            httpx.HTTPError: 当远程请求或 HTTP 状态异常时抛出。
            OSError: 当底层网络读写失败时抛出。
        """
        max_size = self.render_config.remote_resource_max_size_kb * 1024
        if not self._is_allowed_resource_url(url):
            self._log_blocked_resource(
                url, blocked_urls if blocked_urls is not None else set()
            )
            raise RenderResourceRedirectError(
                "Render resource URL is not allowed"
            )
        async with create_async_client(
            timeout=httpx.Timeout(timeout),
            follow_redirects=False,
            proxy_url=self._proxy_url,
        ) as client:
            current_url = url
            redirect_limit = (
                self.render_config.remote_resource_max_redirects
                if max_redirects is None
                else max_redirects
            )
            for redirect_count in range(redirect_limit + 1):
                async with client.stream(
                    "GET", current_url, headers=request_headers
                ) as response:
                    location = response.headers.get("location")
                    if response.is_redirect and location:
                        if redirect_count >= redirect_limit:
                            self.logger.warning(
                                "Render resource redirect limit exceeded: %s",
                                self._sanitize_url_for_log(current_url),
                            )
                            raise RenderResourceRedirectError(
                                "Render resource redirect limit exceeded"
                            )
                        next_url = urljoin(current_url, location)
                        if not self._is_allowed_resource_url(next_url):
                            self._log_blocked_resource(
                                next_url,
                                blocked_urls if blocked_urls is not None else set(),
                            )
                            raise RenderResourceRedirectError(
                                "Render resource redirect target blocked"
                            )
                        current_url = next_url
                        continue

                    response.raise_for_status()
                    content_type = response.headers.get(
                        "content-type"
                    ) or self._guess_content_type(current_url)

                    content_length = response.headers.get("content-length")
                    if content_length is not None:
                        try:
                            length = int(content_length)
                        except ValueError:
                            pass  # Non-numeric content-length, enforce during streaming
                        else:
                            if length > max_size:
                                raise ValueError(
                                    f"Remote resource too large: content-length {content_length} exceeds limit {max_size} bytes"
                                )

                    chunks: list[bytes] = []
                    total_size = 0
                    async for chunk in response.aiter_bytes():
                        total_size += len(chunk)
                        if total_size > max_size:
                            raise ValueError(
                                f"Remote resource too large: exceeds limit {max_size} bytes"
                            )
                        chunks.append(chunk)
                    body = b"".join(chunks)
                    return content_type, body

            raise RenderResourceRedirectError(
                "Render resource redirect limit exceeded"
            )

    def _cached_resource_headers(
        self, ttl: int, resource_type: str, content_type: str
    ) -> dict[str, str]:
        """构造缓存资源响应头。

        Args:
            ttl: 浏览器端缓存有效期，单位为秒。
            resource_type: Playwright 识别的资源类型。
            content_type: 资源 Content-Type。

        Returns:
            响应头字典；字体资源会额外允许跨域访问。
        """
        headers = {"Cache-Control": f"public, max-age={ttl}"}
        content_type_lower = content_type.lower()
        if resource_type == "font" or content_type_lower.startswith("font/"):
            headers["Access-Control-Allow-Origin"] = "*"
        return headers

    def _read_cached_resource_body(self, body_path: Path) -> bytes | None:
        """读取缓存资源正文。

        Args:
            body_path: 缓存正文文件路径。

        Returns:
            文件字节内容；读取失败时返回 None。
        """
        try:
            return body_path.read_bytes()
        except OSError:
            return None

    def _read_cached_resource_meta(self, meta_path: Path) -> dict[str, Any] | None:
        """读取并校验缓存资源元数据。

        Args:
            meta_path: 缓存元数据文件路径。

        Returns:
            规范化后的元数据字典；文件缺失、格式非法或字段不完整时返回 None。
        """
        try:
            data = json.loads(meta_path.read_text(encoding="utf-8"))
            if (
                not isinstance(data, dict)
                or "url" not in data
                or "content_type" not in data
                or "fetched_at" not in data
            ):
                return None
            data["fetched_at"] = float(data["fetched_at"])
            return data
        except (OSError, ValueError, json.JSONDecodeError):
            return None

    def _write_cached_resource(
        self,
        body_path: Path,
        meta_path: Path,
        url: str,
        content_type: str,
        body: bytes,
        fetched_at: float,
    ) -> None:
        """原子写入远程资源缓存正文和元数据。

        Args:
            body_path: 缓存正文目标路径。
            meta_path: 缓存元数据目标路径。
            url: 远程资源 URL。
            content_type: 资源 Content-Type。
            body: 资源字节内容。
            fetched_at: 资源获取时间戳。

        Side Effects:
            在资源缓存目录创建临时文件并替换目标缓存文件；写入失败时记录警告日志。
        """
        try:
            # Atomically write body file
            body_tmp = tempfile.NamedTemporaryFile(
                dir=self.resource_cache_dir, suffix=".body.tmp", delete=False
            )
            try:
                body_tmp.write(body)
                body_tmp.flush()
                os.fsync(body_tmp.fileno())
                body_tmp.close()
                os.replace(body_tmp.name, str(body_path))
            except BaseException:
                body_tmp.close()
                os.unlink(body_tmp.name)
                raise

            # Atomically write meta file (JSON format)
            meta_data = {
                "url": url,
                "content_type": content_type,
                "fetched_at": fetched_at,
                "stored_at": datetime.now(timezone.utc).isoformat(),
                "size": len(body),
            }
            meta_tmp = tempfile.NamedTemporaryFile(
                dir=self.resource_cache_dir,
                suffix=".meta.tmp",
                delete=False,
                mode="w",
                encoding="utf-8",
            )
            try:
                meta_tmp.write(json.dumps(meta_data, ensure_ascii=False))
                meta_tmp.flush()
                os.fsync(meta_tmp.fileno())
                meta_tmp.close()
                os.replace(meta_tmp.name, str(meta_path))
            except BaseException:
                meta_tmp.close()
                os.unlink(meta_tmp.name)
                raise
        except OSError as e:
            self.logger.warning(
                "Failed to write render resource cache for %s: %s",
                self._sanitize_url_for_log(url),
                e,
            )

    def _rewrite_css_urls(self, body: bytes, base_url: str, content_type: str) -> bytes:
        """将 CSS 中的相对 url() 地址改写为绝对地址。

        Args:
            body: 资源字节内容。
            base_url: 用于解析相对路径的原始 CSS URL。
            content_type: 资源 Content-Type。

        Returns:
            改写后的 CSS 字节内容；非 CSS 或无法按 UTF-8 解码时返回原始内容。
        """
        if "css" not in content_type.lower():
            return body
        try:
            css = body.decode("utf-8")
        except UnicodeDecodeError:
            return body

        def rewrite_match(match: Any) -> str:
            """改写单个 CSS url() 匹配项。

            Args:
                match: 正则匹配对象，包含 url() 的前缀、原始地址和后缀。

            Returns:
                原始匹配文本或包含绝对地址的新 url() 文本。
            """
            prefix = match.group(1)
            raw_url = match.group(2).strip()
            suffix = match.group(3)
            if raw_url.startswith(("data:", "http://", "https://")):
                return match.group(0)
            return f"{prefix}{urljoin(base_url, raw_url)}{suffix}"

        rewritten = re.sub(
            r"(url\(\s*['\"]?)([^)'\"\s]+?)(['\"]?\s*\))", rewrite_match, css
        )
        return rewritten.encode("utf-8")

    def _guess_content_type(self, url: str) -> str:
        """根据 URL 路径推断远程资源 Content-Type。

        Args:
            url: 远程资源 URL。

        Returns:
            推断出的 Content-Type；无法识别时返回 application/octet-stream。
        """
        path = urlparse(url).path.lower()
        if path.endswith(".css") or "fonts.googleapis" in url:
            return "text/css; charset=utf-8"
        if path.endswith(".woff2"):
            return "font/woff2"
        if path.endswith(".woff"):
            return "font/woff"
        if path.endswith(".ttf"):
            return "font/ttf"
        return "application/octet-stream"

    def _sanitize_url_for_log(self, url: str) -> str:
        """生成适合日志输出的 URL 摘要。

        Args:
            url: 原始 URL。

        Returns:
            仅包含主机名和路径的字符串，避免记录查询参数。
        """
        parsed = urlparse(url)
        hostname = parsed.hostname or "unknown"
        path = parsed.path or "/"
        return f"{hostname}{path}"

    def _resource_url_for_log(self, url: str) -> str:
        """Format render resource URLs; preserve font CSS query strings for diagnosis."""
        parsed = urlparse(url)
        sanitized = self._sanitize_url_for_log(url)
        hostname = (parsed.hostname or "").lower()
        if parsed.query and any(
            hostname == suffix or hostname.endswith(f".{suffix}")
            for suffix in _CACHEABLE_HOST_SUFFIXES
        ):
            return f"{sanitized}?{parsed.query[:240]}"
        return sanitized

    def _generate_filename(
        self, template_name: str, now: datetime | None = None
    ) -> str:
        """Generate unique filename for the output image.

        Args:
            template_name: Template name to include in filename.
            now: Optional business-time snapshot for the output filename.

        Returns:
            Filename in format "{template}_{timestamp}.jpg".
        """
        if now is None:
            now = now_business()
        timestamp = now.strftime("%Y%m%d_%H%M%S")
        return f"{template_name}_{timestamp}.jpg"

    def _write_file_atomic(self, filename: str, content: bytes) -> None:
        """Atomically write content to file using temp file + rename.

        Args:
            filename: Target filename.
            content: Content to write.

        Raises:
            RenderError: If file write fails.
        """
        target_path = self.images_dir / filename

        try:
            # Create temp file in same directory for atomic rename
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=self.images_dir,
                prefix=f".{filename}",
                suffix=".tmp",
                delete=False,
            ) as tmp_file:
                tmp_file.write(content)
                tmp_path = tmp_file.name

            # Atomic rename
            os.replace(tmp_path, target_path)

        except Exception as e:
            self.logger.error("Failed to write file %s: %s", filename, e)
            # Clean up temp file if it exists
            if "tmp_path" in locals() and Path(tmp_path).exists():
                Path(tmp_path).unlink(missing_ok=True)
            raise RenderError(
                message=f"Failed to write file {filename}: {e}",
            ) from e
