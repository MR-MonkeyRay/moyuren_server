"""Browser instance manager for Playwright."""

import asyncio
import logging
from typing import Any

from playwright.async_api import Browser, Error as PlaywrightError, Page, Playwright, async_playwright

from app.core.network import playwright_proxy_config, redact_url, safe_exception_for_log

logger = logging.getLogger(__name__)

# Shutdown timeout in seconds
_SHUTDOWN_TIMEOUT = 30.0
_SHUTDOWN_POLL_INTERVAL = 0.1


class BrowserManager:
    """Singleton browser manager with lazy initialization and graceful shutdown."""

    def __init__(self, logger_instance: logging.Logger | None = None) -> None:
        """初始化浏览器管理器。

        Args:
            logger_instance: 可选日志记录器；未提供时使用当前模块 logger。

        Side Effects:
            创建异步锁和运行时状态计数器，但不会立即启动 Playwright 或浏览器。
        """
        self._logger = logger_instance or logger
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._browser_disconnected: bool = False
        self._lock = asyncio.Lock()
        self._active_pages: int = 0
        self._shutting_down: bool = False
        self._proxy_url: str | None = None

    @property
    def is_ready(self) -> bool:
        """Return whether the managed browser exists and is connected."""
        return self._browser is not None and self._browser.is_connected()

    def configure(self, logger_instance: logging.Logger, proxy_url: str | None = None) -> None:
        """Configure the browser manager with logger and optional proxy settings."""
        if self._browser is not None and proxy_url != self._proxy_url:
            raise RuntimeError("Cannot change browser proxy after browser launch")
        self._logger = logger_instance
        self._proxy_url = proxy_url

    async def warmup(self) -> None:
        """Pre-initialize the browser to avoid cold start latency.

        This method can be called during application startup to ensure
        the browser is ready before the first render request.

        Raises:
            RuntimeError: If browser manager is shutting down.
        """
        await self._ensure_browser()
        self._logger.info("Browser warmup completed")

    def _on_browser_disconnected(self, browser: Browser) -> None:
        """Mark an unexpected disconnect for recovery on the next request."""
        if self._shutting_down or self._browser is not browser or self._browser_disconnected:
            return
        self._browser_disconnected = True
        self._logger.warning("Chromium browser disconnected; it will be relaunched on the next request")

    async def _close_browser(self) -> None:
        """Reset and close browser resources while the caller holds the lock."""
        browser, playwright = self._browser, self._playwright
        self._browser = None
        self._playwright = None
        self._browser_disconnected = False
        try:
            if browser is not None:
                try:
                    await browser.close()
                except Exception as e:
                    self._logger.warning(f"Failed to close browser: {safe_exception_for_log(e)}")
        finally:
            if playwright is not None:
                try:
                    await playwright.stop()
                except Exception as e:
                    self._logger.warning(f"Failed to stop Playwright: {safe_exception_for_log(e)}")

    async def _ensure_browser(self) -> None:
        """Ensure browser is initialized (lazy loading).

        Raises:
            RuntimeError: If browser manager is shutting down.
        """
        if self._shutting_down:
            raise RuntimeError("Browser manager is shutting down")
        if self._browser is not None and not self._browser_disconnected and self._browser.is_connected():
            return
        async with self._lock:
            if self._shutting_down:
                raise RuntimeError("Browser manager is shutting down")
            if self._browser is not None and not self._browser_disconnected and self._browser.is_connected():
                return
            await self._close_browser()
            if self._shutting_down:
                raise RuntimeError("Browser manager is shutting down")
            pw: Playwright | None = None
            try:
                pw = await async_playwright().start()
                if self._shutting_down:
                    raise RuntimeError("Browser manager is shutting down")
                proxy = playwright_proxy_config(self._proxy_url)
                launch_kwargs: dict[str, Any] = {"headless": True}
                if proxy:
                    launch_kwargs["proxy"] = proxy
                self._browser = await pw.chromium.launch(**launch_kwargs)
                self._playwright = pw
                self._browser.on("disconnected", self._on_browser_disconnected)
                if self._proxy_url:
                    self._logger.info("Chromium browser launched with proxy %s", redact_url(self._proxy_url))
                else:
                    self._logger.info("Chromium browser launched")
            except asyncio.CancelledError:
                # Handle cancellation during browser initialization
                self._logger.info("Browser initialization cancelled")
                if pw is not None:
                    try:
                        await pw.stop()
                    except Exception as stop_err:
                        self._logger.warning(f"Failed to stop Playwright after cancellation: {safe_exception_for_log(stop_err)}")
                self._playwright = None
                self._browser = None
                raise
            except Exception:
                # Clean up playwright if browser launch failed
                if pw is not None:
                    try:
                        await pw.stop()
                    except Exception as stop_err:
                        self._logger.warning(f"Failed to stop Playwright after launch failure: {safe_exception_for_log(stop_err)}")
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
        async with self._lock:
            if self._shutting_down:
                raise RuntimeError("Browser manager is shutting down")
            if self._browser is None:
                raise RuntimeError("Browser is not initialized")
            browser = self._browser
            self._active_pages += 1

        try:
            width = viewport.get("width")
            height = viewport.get("height")
            device_scale_factor = viewport.get("device_scale_factor")
            page_kwargs: dict[str, Any] = {
                "viewport": {"width": width, "height": height},
                "device_scale_factor": device_scale_factor,
            }
            retried = False
            while True:
                try:
                    return await browser.new_page(**page_kwargs)
                except Exception as e:
                    browser_closed_error = isinstance(e, PlaywrightError) and any(
                        message in str(e).lower()
                        for message in (
                            "browser has been closed",
                            "browser is closed",
                            "browser closed",
                            "connection closed",
                            "connection is closed",
                        )
                    )
                    if retried or (not browser_closed_error and browser.is_connected()):
                        raise
                    async with self._lock:
                        if self._browser is browser:
                            self._browser_disconnected = True
                    await self._ensure_browser()
                    async with self._lock:
                        if self._shutting_down:
                            raise RuntimeError("Browser manager is shutting down")
                        if self._browser is None:
                            raise RuntimeError("Browser is not initialized")
                        browser = self._browser
                    retried = True
        except asyncio.CancelledError:
            # Handle cancellation - must rollback _active_pages counter
            async with self._lock:
                self._active_pages = max(0, self._active_pages - 1)
            raise
        except Exception:
            async with self._lock:
                self._active_pages = max(0, self._active_pages - 1)
            raise

    async def release_page(self, page: Page) -> None:
        """Release a page and decrement active page count.

        Args:
            page: The Playwright Page instance to close.
        """
        try:
            await page.close()
        except Exception as e:
            self._logger.warning(f"Failed to close page: {safe_exception_for_log(e)}")
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
            await self._close_browser()
            self._active_pages = 0
            self._shutting_down = False
            self._logger.info("Browser manager shutdown complete")


browser_manager = BrowserManager()
