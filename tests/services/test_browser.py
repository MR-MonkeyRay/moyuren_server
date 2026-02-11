"""Tests for app/services/browser.py - browser manager."""

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.browser import BrowserManager


class TestBrowserManager:
    """Tests for BrowserManager class."""

    @pytest.fixture
    def manager(self, logger: logging.Logger) -> BrowserManager:
        """Create a BrowserManager instance."""
        return BrowserManager(logger_instance=logger)

    def test_init(self, manager: BrowserManager) -> None:
        """Test browser manager initialization."""
        assert manager._browser is None
        assert manager._playwright is None

    def test_configure(self, manager: BrowserManager) -> None:
        """Test configure with logger."""
        new_logger = logging.getLogger("new_test")
        manager.configure(new_logger)
        assert manager._logger is new_logger

    @pytest.mark.asyncio
    async def test_ensure_browser_lazy_init(self, manager: BrowserManager) -> None:
        """Test _ensure_browser lazy initialization."""
        mock_browser = MagicMock()

        mock_playwright_instance = AsyncMock()
        mock_playwright_instance.chromium.launch = AsyncMock(return_value=mock_browser)

        with patch("app.services.browser.async_playwright") as mock_async_pw:
            mock_async_pw.return_value.start = AsyncMock(return_value=mock_playwright_instance)

            await manager._ensure_browser()

            assert manager._playwright is not None
            assert manager._browser is not None

    @pytest.mark.asyncio
    async def test_ensure_browser_already_initialized(self, manager: BrowserManager) -> None:
        """Test _ensure_browser when already initialized."""
        manager._browser = MagicMock()

        with patch("app.services.browser.async_playwright") as mock_async_pw:
            await manager._ensure_browser()

            # Should not call async_playwright
            mock_async_pw.assert_not_called()

    @pytest.mark.asyncio
    async def test_create_page(self, manager: BrowserManager) -> None:
        """Test create_page."""
        mock_page = MagicMock()
        mock_browser = AsyncMock()
        mock_browser.new_page = AsyncMock(return_value=mock_page)

        manager._browser = mock_browser

        viewport = {"width": 800, "height": 600, "device_scale_factor": 2}
        page = await manager.create_page(viewport)

        assert page is mock_page
        mock_browser.new_page.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_page_browser_not_initialized(self, manager: BrowserManager) -> None:
        """Test create_page raises when browser not initialized."""
        # Mock _ensure_browser to not actually initialize
        manager._ensure_browser = AsyncMock()
        manager._browser = None

        viewport = {"width": 800, "height": 600, "device_scale_factor": 2}

        with pytest.raises(RuntimeError, match="Browser is not initialized"):
            await manager.create_page(viewport)

    @pytest.mark.asyncio
    async def test_shutdown(self, manager: BrowserManager) -> None:
        """Test shutdown."""
        mock_browser = AsyncMock()
        mock_playwright = AsyncMock()

        manager._browser = mock_browser
        manager._playwright = mock_playwright

        await manager.shutdown()

        mock_browser.close.assert_called_once()
        mock_playwright.stop.assert_called_once()
        assert manager._browser is None
        assert manager._playwright is None

    @pytest.mark.asyncio
    async def test_shutdown_handles_close_error(self, manager: BrowserManager) -> None:
        """Test shutdown handles browser close error."""
        mock_browser = AsyncMock()
        mock_browser.close = AsyncMock(side_effect=Exception("Close failed"))
        mock_playwright = AsyncMock()

        manager._browser = mock_browser
        manager._playwright = mock_playwright

        # Should not raise
        await manager.shutdown()

        assert manager._browser is None
        assert manager._playwright is None

    @pytest.mark.asyncio
    async def test_shutdown_handles_stop_error(self, manager: BrowserManager) -> None:
        """Test shutdown handles playwright stop error."""
        mock_browser = AsyncMock()
        mock_playwright = AsyncMock()
        mock_playwright.stop = AsyncMock(side_effect=Exception("Stop failed"))

        manager._browser = mock_browser
        manager._playwright = mock_playwright

        # Should not raise
        await manager.shutdown()

        assert manager._browser is None
        assert manager._playwright is None

    @pytest.mark.asyncio
    async def test_shutdown_when_not_initialized(self, manager: BrowserManager) -> None:
        """Test shutdown when not initialized."""
        # Should not raise
        await manager.shutdown()

        assert manager._browser is None
        assert manager._playwright is None

    # --- warmup ---

    @pytest.mark.asyncio
    async def test_warmup(self, manager: BrowserManager) -> None:
        """Test warmup delegates to _ensure_browser."""
        manager._ensure_browser = AsyncMock()
        await manager.warmup()
        manager._ensure_browser.assert_awaited_once()

    # --- _ensure_browser: shutting_down guards ---

    @pytest.mark.asyncio
    async def test_ensure_browser_shutting_down_before_lock(self, manager: BrowserManager) -> None:
        """Test _ensure_browser raises when shutting_down (fast path)."""
        manager._shutting_down = True
        with pytest.raises(RuntimeError, match="shutting down"):
            await manager._ensure_browser()

    @pytest.mark.asyncio
    async def test_ensure_browser_shutting_down_inside_lock(self, manager: BrowserManager) -> None:
        """Test _ensure_browser raises when shutting_down detected inside lock."""

        class _FakeCtx:
            async def __aenter__(self_ctx):
                # Simulate another coroutine setting shutting_down while we waited for lock
                manager._shutting_down = True
                return self_ctx

            async def __aexit__(self_ctx, *args):
                pass

        manager._lock = _FakeCtx()  # type: ignore[assignment]

        with pytest.raises(RuntimeError, match="shutting down"):
            await manager._ensure_browser()

    # --- _ensure_browser: start() failure ---

    @pytest.mark.asyncio
    async def test_ensure_browser_start_failure(self, manager: BrowserManager) -> None:
        """Test _ensure_browser when async_playwright().start() itself fails."""
        with patch("app.services.browser.async_playwright") as mock_async_pw:
            mock_async_pw.return_value.start = AsyncMock(side_effect=RuntimeError("start boom"))

            with pytest.raises(RuntimeError, match="start boom"):
                await manager._ensure_browser()

            assert manager._browser is None
            assert manager._playwright is None

    # --- _ensure_browser: launch failure cleanup ---

    @pytest.mark.asyncio
    async def test_ensure_browser_launch_failure_cleans_up_pw(self, manager: BrowserManager) -> None:
        """Test _ensure_browser cleans up playwright when chromium.launch fails."""
        mock_pw = AsyncMock()
        mock_pw.chromium.launch = AsyncMock(side_effect=RuntimeError("launch boom"))

        with patch("app.services.browser.async_playwright") as mock_async_pw:
            mock_async_pw.return_value.start = AsyncMock(return_value=mock_pw)

            with pytest.raises(RuntimeError, match="launch boom"):
                await manager._ensure_browser()

            mock_pw.stop.assert_awaited_once()
            assert manager._browser is None
            assert manager._playwright is None

    @pytest.mark.asyncio
    async def test_ensure_browser_launch_failure_pw_stop_also_fails(self, manager: BrowserManager) -> None:
        """Test _ensure_browser handles pw.stop() failure after launch failure."""
        mock_pw = AsyncMock()
        mock_pw.chromium.launch = AsyncMock(side_effect=RuntimeError("launch boom"))
        mock_pw.stop = AsyncMock(side_effect=Exception("stop boom"))

        with patch("app.services.browser.async_playwright") as mock_async_pw:
            mock_async_pw.return_value.start = AsyncMock(return_value=mock_pw)

            with pytest.raises(RuntimeError, match="launch boom"):
                await manager._ensure_browser()

            assert manager._browser is None
            assert manager._playwright is None

    # --- _ensure_browser: CancelledError ---

    @pytest.mark.asyncio
    async def test_ensure_browser_cancelled_cleans_up_pw(self, manager: BrowserManager) -> None:
        """Test _ensure_browser cleans up playwright on CancelledError."""
        mock_pw = AsyncMock()
        mock_pw.chromium.launch = AsyncMock(side_effect=asyncio.CancelledError())

        with patch("app.services.browser.async_playwright") as mock_async_pw:
            mock_async_pw.return_value.start = AsyncMock(return_value=mock_pw)

            with pytest.raises(asyncio.CancelledError):
                await manager._ensure_browser()

            mock_pw.stop.assert_awaited_once()
            assert manager._browser is None
            assert manager._playwright is None

    @pytest.mark.asyncio
    async def test_ensure_browser_cancelled_pw_stop_fails(self, manager: BrowserManager) -> None:
        """Test _ensure_browser handles pw.stop() failure after cancellation."""
        mock_pw = AsyncMock()
        mock_pw.chromium.launch = AsyncMock(side_effect=asyncio.CancelledError())
        mock_pw.stop = AsyncMock(side_effect=Exception("stop boom"))

        with patch("app.services.browser.async_playwright") as mock_async_pw:
            mock_async_pw.return_value.start = AsyncMock(return_value=mock_pw)

            with pytest.raises(asyncio.CancelledError):
                await manager._ensure_browser()

            assert manager._browser is None
            assert manager._playwright is None

    # --- _ensure_browser: double-check inside lock ---

    @pytest.mark.asyncio
    async def test_ensure_browser_already_initialized_inside_lock(self, manager: BrowserManager) -> None:
        """Test _ensure_browser skips launch when browser set by another coroutine inside lock."""
        mock_browser = MagicMock()

        class _FakeCtx:
            async def __aenter__(self_ctx):
                # Simulate another coroutine initialized browser while we waited
                manager._browser = mock_browser
                return self_ctx

            async def __aexit__(self_ctx, *args):
                pass

        manager._lock = _FakeCtx()  # type: ignore[assignment]

        with patch("app.services.browser.async_playwright") as mock_async_pw:
            await manager._ensure_browser()
            # Should not call async_playwright since browser was set inside lock
            mock_async_pw.assert_not_called()

    # --- create_page: shutting_down guards ---

    @pytest.mark.asyncio
    async def test_create_page_shutting_down_before_ensure(self, manager: BrowserManager) -> None:
        """Test create_page raises when shutting_down (fast path)."""
        manager._shutting_down = True
        with pytest.raises(RuntimeError, match="shutting down"):
            await manager.create_page({"width": 800, "height": 600, "device_scale_factor": 1})

    @pytest.mark.asyncio
    async def test_create_page_shutting_down_inside_lock(self, manager: BrowserManager) -> None:
        """Test create_page raises when shutting_down detected inside lock."""
        manager._browser = MagicMock()
        manager._ensure_browser = AsyncMock()

        class _FakeCtx:
            async def __aenter__(self_ctx):
                manager._shutting_down = True
                return self_ctx

            async def __aexit__(self_ctx, *args):
                pass

        manager._lock = _FakeCtx()  # type: ignore[assignment]

        with pytest.raises(RuntimeError, match="shutting down"):
            await manager.create_page({"width": 800, "height": 600, "device_scale_factor": 1})

    # --- create_page: error rollback ---

    @pytest.mark.asyncio
    async def test_create_page_new_page_error_decrements_counter(self, manager: BrowserManager) -> None:
        """Test create_page decrements _active_pages on new_page failure."""
        mock_browser = AsyncMock()
        mock_browser.new_page = AsyncMock(side_effect=RuntimeError("page boom"))
        manager._browser = mock_browser
        manager._ensure_browser = AsyncMock()

        with pytest.raises(RuntimeError, match="page boom"):
            await manager.create_page({"width": 800, "height": 600, "device_scale_factor": 1})

        assert manager._active_pages == 0

    @pytest.mark.asyncio
    async def test_create_page_cancelled_decrements_counter(self, manager: BrowserManager) -> None:
        """Test create_page decrements _active_pages on CancelledError."""
        mock_browser = AsyncMock()
        mock_browser.new_page = AsyncMock(side_effect=asyncio.CancelledError())
        manager._browser = mock_browser
        manager._ensure_browser = AsyncMock()

        with pytest.raises(asyncio.CancelledError):
            await manager.create_page({"width": 800, "height": 600, "device_scale_factor": 1})

        assert manager._active_pages == 0

    # --- release_page ---

    @pytest.mark.asyncio
    async def test_release_page(self, manager: BrowserManager) -> None:
        """Test release_page closes page and decrements counter."""
        mock_page = AsyncMock()
        manager._active_pages = 1

        await manager.release_page(mock_page)

        mock_page.close.assert_awaited_once()
        assert manager._active_pages == 0

    @pytest.mark.asyncio
    async def test_release_page_close_error(self, manager: BrowserManager) -> None:
        """Test release_page still decrements counter when close fails."""
        mock_page = AsyncMock()
        mock_page.close = AsyncMock(side_effect=Exception("close boom"))
        manager._active_pages = 1

        await manager.release_page(mock_page)

        assert manager._active_pages == 0

    @pytest.mark.asyncio
    async def test_release_page_counter_never_negative(self, manager: BrowserManager) -> None:
        """Test release_page clamps counter to 0."""
        mock_page = AsyncMock()
        manager._active_pages = 0

        await manager.release_page(mock_page)

        assert manager._active_pages == 0

    # --- shutdown: active pages timeout ---

    @pytest.mark.asyncio
    async def test_shutdown_waits_for_active_pages(self, manager: BrowserManager) -> None:
        """Test shutdown waits for active pages then closes."""
        mock_browser = AsyncMock()
        mock_playwright = AsyncMock()
        manager._browser = mock_browser
        manager._playwright = mock_playwright
        manager._active_pages = 1

        async def _simulate_page_release():
            await asyncio.sleep(0.05)
            manager._active_pages = 0

        asyncio.create_task(_simulate_page_release())

        await manager.shutdown()

        mock_browser.close.assert_awaited_once()
        mock_playwright.stop.assert_awaited_once()
        assert manager._active_pages == 0

    @pytest.mark.asyncio
    async def test_shutdown_timeout_with_active_pages(self, manager: BrowserManager) -> None:
        """Test shutdown proceeds after timeout even with active pages."""
        mock_browser = AsyncMock()
        mock_playwright = AsyncMock()
        manager._browser = mock_browser
        manager._playwright = mock_playwright
        manager._active_pages = 1

        # Patch timeout to be very short
        with patch("app.services.browser._SHUTDOWN_TIMEOUT", 0.05), \
             patch("app.services.browser._SHUTDOWN_POLL_INTERVAL", 0.01):
            await manager.shutdown()

        mock_browser.close.assert_awaited_once()
        assert manager._browser is None
        assert manager._active_pages == 0

    # --- module-level singleton ---

    def test_module_level_browser_manager_exists(self) -> None:
        """Test that module-level browser_manager singleton is created."""
        from app.services.browser import browser_manager

        assert isinstance(browser_manager, BrowserManager)
