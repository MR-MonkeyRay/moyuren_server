"""Image rendering service module."""

import logging
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, TemplateError
from markupsafe import Markup, escape

from app.core.config import TemplateRenderConfig, TemplatesConfig, ViewportConfig
from app.core.errors import RenderError
from app.services.browser import browser_manager


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
    return Markup(str(escaped).replace("\n", "<br>\n"))


class ImageRenderer:
    """HTML template renderer and screenshot generator using reusable browser."""

    def __init__(
        self,
        templates_config: TemplatesConfig,
        static_dir: str,
        render_config: TemplateRenderConfig,
        logger: logging.Logger,
    ) -> None:
        """Initialize the image renderer.

        Args:
            templates_config: Templates configuration.
            static_dir: Directory where generated images will be saved.
            render_config: Render configuration including viewport and quality settings.
            logger: Logger instance for logging render status.
        """
        self.templates_config = templates_config
        self.static_dir = Path(static_dir)
        self.render_config = render_config
        self.logger = logger
        self._env_cache: dict[str, Environment] = {}

        # Ensure static directory exists
        self.static_dir.mkdir(parents=True, exist_ok=True)

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
        template_item = self.templates_config.get_template(template_name)

        # Step 1: Render HTML with Jinja2
        html_content = self._render_template(data, template_item.path)

        # Step 2: Generate screenshot with Playwright
        viewport = template_item.viewport
        jpeg_quality = template_item.jpeg_quality or self.render_config.jpeg_quality
        device_scale_factor = template_item.device_scale_factor or self.render_config.device_scale_factor
        image_bytes = await self._generate_screenshot(html_content, viewport, jpeg_quality, device_scale_factor)

        # Step 3: Atomically write to file
        filename = self._generate_filename(template_item.name)
        self._write_file_atomic(filename, image_bytes)

        self.logger.info(f"Successfully rendered image: {filename}")
        return filename

    def _render_template(self, data: dict[str, Any], template_path: str) -> str:
        """Render Jinja2 template with provided data.

        Args:
            data: Template context data.
            template_path: Template file path.

        Returns:
            Rendered HTML content.

        Raises:
            RenderError: If template rendering fails.
        """
        try:
            env = self._get_jinja_env(template_path)
            template_name = Path(template_path).name
            template = env.get_template(template_name)
            render_context = {
                **data,
                "use_china_cdn": self.render_config.use_china_cdn,
            }
            return template.render(**render_context)
        except TemplateError as e:
            self.logger.error(f"Template rendering failed: {e}")
            raise RenderError(
                message="Failed to render template",
                detail=str(e),
            ) from e

    async def _generate_screenshot(
        self,
        html_content: str,
        viewport: ViewportConfig,
        jpeg_quality: int,
        device_scale_factor: int,
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
            page = await browser_manager.create_page({
                "width": viewport.width,
                "height": viewport.height,
                "device_scale_factor": device_scale_factor,
            })

            # Set HTML content and wait for network idle
            await page.set_content(html_content, wait_until="networkidle")

            # Take screenshot as JPEG
            screenshot_bytes = await page.screenshot(
                type="jpeg",
                quality=jpeg_quality,
                full_page=True,
            )

            return screenshot_bytes

        except Exception as e:
            self.logger.error(f"Screenshot generation failed: {e}")
            raise RenderError(
                message="Failed to generate screenshot",
                detail=str(e),
            ) from e
        finally:
            if page is not None:
                await browser_manager.release_page(page)

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
        target_path = self.static_dir / filename

        try:
            # Create temp file in same directory for atomic rename
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=self.static_dir,
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
                message=f"Failed to write file: {filename}",
                detail=str(e),
            ) from e
