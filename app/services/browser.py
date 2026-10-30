"""Playwright 浏览器实例管理器。"""

import asyncio
import logging
from typing import Any

from playwright.async_api import Browser, Error as PlaywrightError, Page, Playwright, async_playwright

from app.core.network import playwright_proxy_config, redact_url, safe_exception_for_log

logger = logging.getLogger(__name__)

# 关闭超时（秒）
_SHUTDOWN_TIMEOUT = 30.0
_SHUTDOWN_POLL_INTERVAL = 0.1


class BrowserManager:
    """单例浏览器管理器：惰性初始化，优雅关闭。"""

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
        """返回托管浏览器是否存在且处于连接状态。"""
        return self._browser is not None and self._browser.is_connected()

    def configure(self, logger_instance: logging.Logger, proxy_url: str | None = None) -> None:
        """配置浏览器管理器的 logger 与可选代理。"""
        if self._browser is not None and proxy_url != self._proxy_url:
            raise RuntimeError("Cannot change browser proxy after browser launch")
        self._logger = logger_instance
        self._proxy_url = proxy_url

    async def warmup(self) -> None:
        """预热浏览器，避免冷启动延迟。

        可在应用启动阶段调用，保证首次渲染请求前浏览器已就绪。

        Raises:
            RuntimeError: 浏览器管理器正在关闭时抛出。
        """
        await self._ensure_browser()
        self._logger.info("Browser warmup completed")

    def _on_browser_disconnected(self, browser: Browser) -> None:
        """标记意外断连，等待下次请求时恢复。"""
        if self._shutting_down or self._browser is not browser or self._browser_disconnected:
            return
        self._browser_disconnected = True
        self._logger.warning("Chromium browser disconnected; it will be relaunched on the next request")

    async def _close_browser(self) -> None:
        """重置并关闭浏览器资源；调用方必须已持有锁。"""
        browser, playwright = self._browser, self._playwright
        self._browser = None
        self._playwright = None
        self._browser_disconnected = False
        try:
            if browser is not None:
                try:
                    await browser.close()
                except Exception as e:
                    self._logger.warning("Failed to close browser: %s", safe_exception_for_log(e))
        finally:
            if playwright is not None:
                try:
                    await playwright.stop()
                except Exception as e:
                    self._logger.warning("Failed to stop Playwright: %s", safe_exception_for_log(e))

    async def _ensure_browser(self) -> None:
        """确保浏览器已初始化（惰性加载）。

        Raises:
            RuntimeError: 浏览器管理器正在关闭时抛出。
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
                # 处理初始化期间的取消
                self._logger.info("Browser initialization cancelled")
                if pw is not None:
                    try:
                        await pw.stop()
                    except Exception as stop_err:
                        self._logger.warning(
                            "Failed to stop Playwright after cancellation: %s", safe_exception_for_log(stop_err)
                        )
                self._playwright = None
                self._browser = None
                raise
            except Exception:
                # 浏览器启动失败时清理 Playwright
                if pw is not None:
                    try:
                        await pw.stop()
                    except Exception as stop_err:
                        self._logger.warning(
                            "Failed to stop Playwright after launch failure: %s",
                            safe_exception_for_log(stop_err),
                        )
                self._playwright = None
                self._browser = None
                raise

    async def create_page(self, viewport: dict[str, Any]) -> Page:
        """按指定 viewport 创建新页面。

        Args:
            viewport: 包含 width、height、device_scale_factor 的字典。

        Returns:
            新的 Playwright Page 实例。

        Raises:
            RuntimeError: 浏览器未初始化或管理器正在关闭时抛出。
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
            # 处理取消——必须回滚 _active_pages 计数
            async with self._lock:
                self._active_pages = max(0, self._active_pages - 1)
            raise
        except Exception:
            async with self._lock:
                self._active_pages = max(0, self._active_pages - 1)
            raise

    async def release_page(self, page: Page) -> None:
        """释放页面并递减活跃页面计数。

        Args:
            page: 待关闭的 Playwright Page 实例。
        """
        try:
            await page.close()
        except Exception as e:
            self._logger.warning("Failed to close page: %s", safe_exception_for_log(e))
        finally:
            async with self._lock:
                self._active_pages = max(0, self._active_pages - 1)

    async def shutdown(self) -> None:
        """关闭浏览器与 Playwright 实例。

        关闭前等待活跃页面释放，达到关闭超时上限后继续关闭。
        """
        self._shutting_down = True
        self._logger.info("Browser manager shutdown initiated")

        # 等待活跃页面完成（带超时）
        elapsed = 0.0
        while self._active_pages > 0 and elapsed < _SHUTDOWN_TIMEOUT:
            self._logger.debug("Waiting for %s active page(s) to complete...", self._active_pages)
            await asyncio.sleep(_SHUTDOWN_POLL_INTERVAL)
            elapsed += _SHUTDOWN_POLL_INTERVAL

        if self._active_pages > 0:
            self._logger.warning(
                "Shutdown timeout: %s page(s) still active after %ss",
                self._active_pages,
                _SHUTDOWN_TIMEOUT,
            )

        async with self._lock:
            await self._close_browser()
            self._active_pages = 0
            self._shutting_down = False
            self._logger.info("Browser manager shutdown complete")


browser_manager = BrowserManager()
