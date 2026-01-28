"""Image rendering service module."""

import logging
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, TemplateError
from playwright.async_api import async_playwright
from app.core.config import RenderConfig
from app.core.errors import RenderError


class ImageRenderer:
    """HTML template renderer and screenshot generator using Playwright."""

    def __init__(
        self,
        template_path: str,
        static_dir: str,
        render_config: RenderConfig,
        logger: logging.Logger,
    ) -> None:
        """Initialize the image renderer.

        Args:
            template_path: Path to the Jinja2 template file.
            static_dir: Directory where generated images will be saved.
            render_config: Render configuration including viewport and quality settings.
            logger: Logger instance for logging render status.
        """
        self.template_path = template_path
        self.static_dir = Path(static_dir)
        self.render_config = render_config
        self.logger = logger

        # Ensure static directory exists
        self.static_dir.mkdir(parents=True, exist_ok=True)

        # Setup Jinja2 environment
        template_dir = str(Path(template_path).parent)
        self.jinja_env = Environment(loader=FileSystemLoader(template_dir))
        self.template_name = Path(template_path).name

    async def render(self, data: dict[str, Any]) -> str:
        """Render HTML template and generate screenshot image.

        Args:
            data: Template context data.

        Returns:
            The filename of the generated image (e.g., "moyuren_20260127_060001.jpg").

        Raises:
            RenderError: If template rendering or screenshot generation fails.
        """
        # Step 1: Render HTML with Jinja2
        html_content = self._render_template(data)

        # Step 2: Generate screenshot with Playwright
        image_bytes = await self._generate_screenshot(html_content)

        # Step 3: Atomically write to file
        filename = self._generate_filename()
        self._write_file_atomic(filename, image_bytes)

        self.logger.info(f"Successfully rendered image: {filename}")
        return filename

    def _render_template(self, data: dict[str, Any]) -> str:
        """Render Jinja2 template with provided data.

        Args:
            data: Template context data.

        Returns:
            Rendered HTML content.

        Raises:
            RenderError: If template rendering fails.
        """
        try:
            template = self.jinja_env.get_template(self.template_name)
            return template.render(**data)
        except TemplateError as e:
            self.logger.error(f"Template rendering failed: {e}")
            raise RenderError(
                message="Failed to render template",
                detail=str(e),
            ) from e

    async def _generate_screenshot(self, html_content: str) -> bytes:
        """Generate screenshot from HTML using Playwright.

        Args:
            html_content: HTML content to render.

        Returns:
            Screenshot image bytes.

        Raises:
            RenderError: If screenshot generation fails.
        """
        browser = None
        try:
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(headless=True)
                page = await browser.new_page(
                    viewport={
                        "width": self.render_config.viewport_width,
                        "height": self.render_config.viewport_height,
                    },
                    device_scale_factor=self.render_config.device_scale_factor,
                )

                # Set HTML content and wait for network idle
                await page.set_content(html_content, wait_until="networkidle")

                # Take screenshot as JPEG
                screenshot_bytes = await page.screenshot(
                    type="jpeg",
                    quality=self.render_config.jpeg_quality,
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
            if browser is not None:
                try:
                    await browser.close()
                except Exception:
                    pass  # Ignore errors during cleanup

    def _generate_filename(self) -> str:
        """Generate unique filename for the output image.

        Returns:
            Filename in format "moyuren_YYYYMMDD_HHMMSS.jpg".
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return f"moyuren_{timestamp}.jpg"

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
