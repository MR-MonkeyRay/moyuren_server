"""Image rendering service module."""

import hashlib
import json
import logging
import os
import re
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from jinja2 import Environment, FileSystemLoader, TemplateError
from markupsafe import Markup, escape

from app.core.config import TemplateItemConfig, TemplateRenderConfig, TemplatesConfig, ViewportConfig
from app.core.errors import RenderError
from app.services.browser import browser_manager

_CACHEABLE_RESOURCE_TYPES = {"stylesheet", "font"}
_CACHEABLE_HOST_SUFFIXES = (
    "fonts.googleapis.com",
    "fonts.googleapis.cn",
    "fonts.gstatic.com",
    "fonts.gstatic.cn",
)


@dataclass
class _CachedResource:
    body: bytes
    content_type: str
    cache_state: str


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
    ) -> None:
        """Initialize the image renderer.

        Args:
            templates_config: Templates configuration.
            images_dir: Directory where generated images will be saved.
            render_config: Render configuration including viewport and quality settings.
            logger: Logger instance for logging render status.
        """
        self.templates_config = templates_config
        self.images_dir = Path(images_dir)
        self.render_config = render_config
        self.logger = logger
        self._env_cache: dict[str, Environment] = {}
        self.resource_cache_dir = self.images_dir.parent / "render_resources"
        self._render_degraded: bool = False

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
    ) -> str:
        """Render HTML template and generate screenshot image.

        Args:
            data: Template context data.
            template_name: Template name to use.

        Returns:
            The filename of the generated image (e.g., "moyuren_moyuren_20260127_060001.jpg").

        Raises:
            RenderError: If template rendering or screenshot generation fails.
        """
        self._render_degraded = False
        template_item = self.templates_config.get_template(template_name)
        render_start = time.monotonic()
        self.logger.info(
            f"Rendering template '{template_item.name}' from {template_item.path} "
            f"(viewport={template_item.viewport.width}x{template_item.viewport.height})"
        )

        # Step 1: Render HTML with Jinja2
        html_content = self._render_template(data, template_item)
        self.logger.info(f"Rendered HTML for '{template_item.name}' ({len(html_content)} bytes)")

        # Step 2: Generate screenshot with Playwright
        viewport = template_item.viewport
        jpeg_quality = template_item.jpeg_quality or self.render_config.jpeg_quality
        device_scale_factor = template_item.device_scale_factor or self.render_config.device_scale_factor
        image_bytes = await self._generate_screenshot(
            html_content,
            viewport,
            jpeg_quality,
            device_scale_factor,
            template_item.name,
        )

        # Step 3: Atomically write to file
        filename = self._generate_filename(template_item.name)
        self._write_file_atomic(filename, image_bytes)

        self.logger.info(
            f"Successfully rendered image: {filename} "
            f"({len(image_bytes)} bytes, {time.monotonic() - render_start:.1f}s)"
        )
        if self._render_degraded:
            self.logger.warning("Render completed with degraded stylesheet(s)")
        return filename

    def _render_template(self, data: dict[str, Any], template_item: TemplateItemConfig) -> str:
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
            self.logger.error(f"Template rendering failed: {e}")
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
        try:
            self.logger.info(
                f"Creating screenshot page for '{template_name}' "
                f"(scale={device_scale_factor}, quality={jpeg_quality})"
            )
            page = await browser_manager.create_page(
                {
                    "width": viewport.width,
                    "height": viewport.height,
                    "device_scale_factor": device_scale_factor,
                }
            )
            await self._install_resource_cache_routes(page)

            # Set HTML content and wait for network idle
            set_content_start = time.monotonic()
            self.logger.info(f"Loading HTML into browser for '{template_name}'")
            await page.set_content(html_content, wait_until="networkidle")
            self.logger.info(
                f"Browser content loaded for '{template_name}' "
                f"({time.monotonic() - set_content_start:.1f}s)"
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
                        f"Screenshot truncated: content height {original_height}px exceeds "
                        f"max {self.MAX_VIEWPORT_HEIGHT}px, output will be clipped"
                    )
                    use_full_page = False
                self.logger.info(
                    f"Adjusting viewport for '{template_name}' "
                    f"from {viewport.height}px to {content_height}px"
                )
                await page.set_viewport_size(
                    {"width": viewport.width, "height": content_height}
                )

            # Take screenshot as JPEG
            screenshot_start = time.monotonic()
            self.logger.info(f"Capturing screenshot for '{template_name}'")
            screenshot_bytes = await page.screenshot(
                type="jpeg",
                quality=jpeg_quality,
                full_page=use_full_page,
            )
            self.logger.info(
                f"Screenshot captured for '{template_name}' "
                f"({len(screenshot_bytes)} bytes, {time.monotonic() - screenshot_start:.1f}s)"
            )

            return screenshot_bytes

        except Exception as e:
            self.logger.error(f"Screenshot generation failed: {e}")
            raise RenderError(
                message=f"Failed to generate screenshot: {e}",
            ) from e
        finally:
            if page is not None:
                await browser_manager.release_page(page)

    async def _install_resource_cache_routes(self, page: Any) -> None:
        """Intercept remote font resources and serve them from a TTL cache."""
        if not self.render_config.remote_resource_cache_enabled:
            self.logger.info("Remote render resource cache disabled")
            return

        ttl = self.render_config.remote_resource_cache_ttl_sec
        timeout = self.render_config.remote_resource_timeout_sec
        self.logger.info(
            f"Remote render resource cache enabled "
            f"(dir={self.resource_cache_dir}, ttl={ttl}s, timeout={timeout}s)"
        )

        async def handle_route(route: Any) -> None:
            request = route.request
            url = request.url
            resource_type = getattr(request, "resource_type", "")
            if not self._should_cache_resource(url, resource_type):
                await route.continue_()
                return

            try:
                cached = await self._get_remote_resource(url, ttl=ttl, timeout=timeout)
            except ValueError:
                # Size limit exceeded - treat as unavailable
                self.logger.warning(f"Render resource rejected (size limit exceeded): {self._sanitize_url_for_log(url)}")
                cached = None
            if cached is not None:
                self.logger.info(f"Render resource {cached.cache_state}: {self._sanitize_url_for_log(url)}")
                await route.fulfill(
                    status=200,
                    content_type=cached.content_type,
                    headers=self._cached_resource_headers(ttl, resource_type, cached.content_type),
                    body=cached.body,
                )
                return

            if resource_type == "stylesheet":
                self.logger.warning(f"Using empty stylesheet fallback for render resource: {self._sanitize_url_for_log(url)}")
                self._render_degraded = True
                await route.fulfill(
                    status=200,
                    content_type="text/css; charset=utf-8",
                    headers={"Cache-Control": "no-store"},
                    body=b"",
                )
                return

            self.logger.warning(f"Aborting uncached render resource after fetch failure: {self._sanitize_url_for_log(url)}")
            await route.abort()

        await page.route("**/*", handle_route)

    def _should_cache_resource(self, url: str, resource_type: str) -> bool:
        if resource_type not in _CACHEABLE_RESOURCE_TYPES:
            return False
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").lower()
        return any(hostname == suffix or hostname.endswith(f".{suffix}") for suffix in _CACHEABLE_HOST_SUFFIXES)

    async def _get_remote_resource(self, url: str, ttl: int, timeout: float) -> _CachedResource | None:
        cache_key = hashlib.sha256(url.encode("utf-8")).hexdigest()
        body_path = self.resource_cache_dir / f"{cache_key}.body"
        meta_path = self.resource_cache_dir / f"{cache_key}.meta"
        now = time.time()

        cached_body = self._read_cached_resource_body(body_path)
        cached_meta = self._read_cached_resource_meta(meta_path)
        age = now - cached_meta["fetched_at"] if cached_meta else None
        if cached_body is not None and cached_meta and age is not None and age <= ttl:
            return _CachedResource(
                body=self._rewrite_css_urls(cached_body, url, cached_meta["content_type"]),
                content_type=cached_meta["content_type"],
                cache_state="cache hit",
            )

        try:
            content_type, body = await self._fetch_remote_resource_async(
                url,
                timeout,
            )
        except (httpx.HTTPError, OSError) as e:
            if cached_body is not None and cached_meta:
                self.logger.warning(
                    f"Render resource refresh failed, using stale cache: {self._sanitize_url_for_log(url)} ({type(e).__name__}: {e})"
                )
                return _CachedResource(
                    body=self._rewrite_css_urls(cached_body, url, cached_meta["content_type"]),
                    content_type=cached_meta["content_type"],
                    cache_state="stale cache",
                )
            self.logger.warning(f"Render resource fetch failed: {self._sanitize_url_for_log(url)} ({type(e).__name__}: {e})")
            return None

        self._write_cached_resource(body_path, meta_path, url, content_type, body, now)
        return _CachedResource(
            body=self._rewrite_css_urls(body, url, content_type),
            content_type=content_type,
            cache_state="cache refresh",
        )

    async def _fetch_remote_resource_async(self, url: str, timeout: float) -> tuple[str, bytes]:
        """Fetch remote resource with streaming size enforcement to prevent OOM."""
        max_size = self.render_config.remote_resource_max_size_kb * 1024
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout), follow_redirects=True) as client:
            # Check content-length header before downloading
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                content_type = response.headers.get("content-type") or self._guess_content_type(url)

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

                # Stream body with progressive size enforcement
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

    def _cached_resource_headers(self, ttl: int, resource_type: str, content_type: str) -> dict[str, str]:
        headers = {"Cache-Control": f"public, max-age={ttl}"}
        content_type_lower = content_type.lower()
        if resource_type == "font" or content_type_lower.startswith("font/"):
            headers["Access-Control-Allow-Origin"] = "*"
        return headers

    def _read_cached_resource_body(self, body_path: Path) -> bytes | None:
        try:
            return body_path.read_bytes()
        except OSError:
            return None

    def _read_cached_resource_meta(self, meta_path: Path) -> dict[str, Any] | None:
        try:
            data = json.loads(meta_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or "url" not in data or "content_type" not in data or "fetched_at" not in data:
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
                dir=self.resource_cache_dir, suffix=".meta.tmp", delete=False, mode="w",
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
            self.logger.warning(f"Failed to write render resource cache for {self._sanitize_url_for_log(url)}: {e}")

    def _rewrite_css_urls(self, body: bytes, base_url: str, content_type: str) -> bytes:
        if "css" not in content_type.lower():
            return body
        try:
            css = body.decode("utf-8")
        except UnicodeDecodeError:
            return body

        def rewrite_match(match: Any) -> str:
            prefix = match.group(1)
            raw_url = match.group(2).strip()
            suffix = match.group(3)
            if raw_url.startswith(("data:", "http://", "https://")):
                return match.group(0)
            return f"{prefix}{urljoin(base_url, raw_url)}{suffix}"

        rewritten = re.sub(r"(url\(\s*['\"]?)([^)'\"\s]+?)(['\"]?\s*\))", rewrite_match, css)
        return rewritten.encode("utf-8")

    def _guess_content_type(self, url: str) -> str:
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
        parsed = urlparse(url)
        hostname = parsed.hostname or "unknown"
        path = parsed.path or "/"
        return f"{hostname}{path}"

    def _generate_filename(self, template_name: str) -> str:
        """Generate unique filename for the output image.

        Args:
            template_name: Template name to include in filename.

        Returns:
            Filename in format "{template}_{timestamp}.jpg".
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
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
            self.logger.error(f"Failed to write file {filename}: {e}")
            # Clean up temp file if it exists
            if "tmp_path" in locals() and Path(tmp_path).exists():
                Path(tmp_path).unlink(missing_ok=True)
            raise RenderError(
                message=f"Failed to write file {filename}: {e}",
            ) from e
