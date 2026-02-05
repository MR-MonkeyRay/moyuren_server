"""Browser instance manager for Playwright."""

import asyncio
import logging
from typing import Any

from playwright.async_api import async_playwright, Browser, Page, Playwright

logger = logging.getLogger(__name__)

# Shutdown timeout in seconds
_SHUTDOWN_TIMEOUT = 30.0
_SHUTDOWN_POLL_INTERVAL = 0.1


class BrowserManager:
    """Singleton browser manager with lazy initialization and graceful shutdown."""

    def __init__(self, logger_instance: logging.Logger | None = None) -> None:
        self._logger = logger_instance or logger
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._lock = asyncio.Lock()
        self._active_pages: int = 0
        self._shutting_down: bool = False

    def configure(self, logger_instance: logging.Logger) -> None:
        """Configure the browser manager with a logger instance."""
        self._logger = logger_instance

    async def warmup(self) -> None:
        """Pre-initialize the browser to avoid cold start latency.

        This method can be called during application startup to ensure
        the browser is ready before the first render request.

        Raises:
            RuntimeError: If browser manager is shutting down.
        """
        await self._ensure_browser()
        self._logger.info("Browser warmup completed")

    async def _ensure_browser(self) -> None:
        """Ensure browser is initialized (lazy loading).

        Raises:
            RuntimeError: If browser manager is shutting down.
        """
        if self._shutting_down:
            raise RuntimeError("Browser manager is shutting down")
        if self._browser is not None:
            return
        async with self._lock:
            if self._shutting_down:
                raise RuntimeError("Browser manager is shutting down")
            if self._browser is not None:
                return
            pw: Playwright | None = None
            try:
                pw = await async_playwright().start()
                self._browser = await pw.chromium.launch(headless=True)
                self._playwright = pw
                self._logger.info("Chromium browser launched")
            except asyncio.CancelledError:
                # Handle cancellation during browser initialization
                self._logger.info("Browser initialization cancelled")
                if pw is not None:
                    try:
                        await pw.stop()
                    except Exception as stop_err:
                        self._logger.warning(f"Failed to stop Playwright after cancellation: {stop_err}")
                self._playwright = None
                self._browser = None
                raise
            except Exception:
                # Clean up playwright if browser launch failed
                if pw is not None:
                    try:
                        await pw.stop()
                    except Exception as stop_err:
                        self._logger.warning(f"Failed to stop Playwright after launch failure: {stop_err}")
                self._playwright = None
                self._browser = None
                raise

    async def create_page(self, viewport: dict[str, Any]) -> Page:
        """Create a new page with specified viewport settings.

        Args:
            viewport: Dictionary with width, height, and device_scale_factor.

        Returns:
            A new Playwright Page instance.

        Raises:
            RuntimeError: If browser is not initialized or manager is shutting down.
        """
        if self._shutting_down:
            raise RuntimeError("Browser manager is shutting down")
        await self._ensure_browser()
        if self._browser is None:
            raise RuntimeError("Browser is not initialized")

        async with self._lock:
            if self._shutting_down:
                raise RuntimeError("Browser manager is shutting down")
            self._active_pages += 1

        try:
            width = viewport.get("width")
            height = viewport.get("height")
            device_scale_factor = viewport.get("device_scale_factor")
            return await self._browser.new_page(
                viewport={"width": width, "height": height},
                device_scale_factor=device_scale_factor,
            )
        except asyncio.CancelledError:
            # Handle cancellation - must rollback _active_pages counter
            async with self._lock:
                self._active_pages -= 1
            raise
        except Exception:
            async with self._lock:
                self._active_pages -= 1
            raise

    async def release_page(self, page: Page) -> None:
        """Release a page and decrement active page count.

        Args:
            page: The Playwright Page instance to close.
        """
        try:
            await page.close()
        except Exception as e:
            self._logger.warning(f"Failed to close page: {e}")
        finally:
            async with self._lock:
                self._active_pages = max(0, self._active_pages - 1)

    async def shutdown(self) -> None:
        """Shutdown browser and Playwright instances.

        Waits for active pages to be released before closing.
        """
        self._shutting_down = True
        self._logger.info("Browser manager shutdown initiated")

        # Wait for active pages to complete (with timeout)
        elapsed = 0.0
        while self._active_pages > 0 and elapsed < _SHUTDOWN_TIMEOUT:
            self._logger.debug(f"Waiting for {self._active_pages} active page(s) to complete...")
            await asyncio.sleep(_SHUTDOWN_POLL_INTERVAL)
            elapsed += _SHUTDOWN_POLL_INTERVAL

        if self._active_pages > 0:
            self._logger.warning(
                f"Shutdown timeout: {self._active_pages} page(s) still active after {_SHUTDOWN_TIMEOUT}s"
            )

        async with self._lock:
            if self._browser is not None:
                try:
                    await self._browser.close()
                except Exception as e:
                    self._logger.warning(f"Failed to close browser: {e}")
            if self._playwright is not None:
                try:
                    await self._playwright.stop()
                except Exception as e:
                    self._logger.warning(f"Failed to stop Playwright: {e}")
            self._browser = None
            self._playwright = None
            self._active_pages = 0
            self._logger.info("Browser manager shutdown complete")


browser_manager = BrowserManager()
