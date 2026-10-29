"""Tests for app/services/browser.py - browser manager."""

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from playwright.async_api import Error as PlaywrightError

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
        manager.configure(new_logger, proxy_url="http://proxy.example:8080")
        assert manager._logger is new_logger
        assert manager._proxy_url == "http://proxy.example:8080"

    def test_configure_rejects_proxy_change_after_launch(self, manager: BrowserManager) -> None:
        """Test changing proxy after browser launch is rejected."""
        manager._browser = MagicMock()

        with pytest.raises(RuntimeError, match="Cannot change browser proxy"):
            manager.configure(logging.getLogger("new_test"), proxy_url="http://proxy.example:8080")

    @pytest.mark.asyncio
    async def test_ensure_browser_lazy_init(self, manager: BrowserManager) -> None:
        """Test _ensure_browser lazy initialization."""
        mock_browser = MagicMock()
        mock_browser.is_connected.return_value = True

        mock_playwright_instance = AsyncMock()
        mock_playwright_instance.chromium.launch = AsyncMock(return_value=mock_browser)

        with patch("app.services.browser.async_playwright") as mock_async_pw:
            mock_async_pw.return_value.start = AsyncMock(return_value=mock_playwright_instance)

            await manager._ensure_browser()

            assert manager._playwright is not None
            assert manager._browser is not None

    @pytest.mark.asyncio
    async def test_ensure_browser_launches_with_proxy(self, manager: BrowserManager) -> None:
        """Test Chromium launch receives Playwright proxy config."""
        manager.configure(logging.getLogger("new_test"), proxy_url="socks5://user:pass@proxy.example:1080")
        mock_browser = MagicMock()
        mock_browser.is_connected.return_value = True

        mock_playwright_instance = AsyncMock()
        mock_playwright_instance.chromium.launch = AsyncMock(return_value=mock_browser)

        with patch("app.services.browser.async_playwright") as mock_async_pw:
            mock_async_pw.return_value.start = AsyncMock(return_value=mock_playwright_instance)

            await manager._ensure_browser()

        mock_playwright_instance.chromium.launch.assert_awaited_once_with(
            headless=True,
            proxy={
                "server": "socks5://proxy.example:1080",
                "username": "user",
                "password": "pass",
            },
        )

    @pytest.mark.asyncio
    async def test_ensure_browser_already_initialized(self, manager: BrowserManager) -> None:
        """Test _ensure_browser when already initialized."""
        manager._browser = MagicMock()
        manager._browser.is_connected.return_value = True

        with patch("app.services.browser.async_playwright") as mock_async_pw:
            await manager._ensure_browser()

            # Should not call async_playwright
            mock_async_pw.assert_not_called()

    @pytest.mark.asyncio
    async def test_ensure_browser_relaunches_disconnected_browser(self, manager: BrowserManager) -> None:
        """Test a disconnected browser is cleaned up and relaunched."""
        page = MagicMock()
        stale_browser = MagicMock()
        stale_browser.is_connected.return_value = False
        stale_browser.close = AsyncMock()
        stale_playwright = MagicMock()
        stale_playwright.stop = AsyncMock()
        fresh_browser = MagicMock()
        fresh_browser.is_connected.return_value = True
        fresh_browser.new_page = AsyncMock(return_value=page)
        fresh_playwright = MagicMock()
        fresh_playwright.chromium.launch = AsyncMock(return_value=fresh_browser)
        manager._browser = stale_browser
        manager._playwright = stale_playwright

        with patch("app.services.browser.async_playwright") as mock_async_pw:
            mock_async_pw.return_value.start = AsyncMock(return_value=fresh_playwright)

            result = await manager.create_page({"width": 800, "height": 600, "device_scale_factor": 1})

        stale_browser.close.assert_awaited_once()
        stale_playwright.stop.assert_awaited_once()
        assert result is page
        assert manager._active_pages == 1
        assert manager._browser is fresh_browser
        assert manager._playwright is fresh_playwright
        await manager.release_page(page)
        assert manager._active_pages == 0

    @pytest.mark.asyncio
    async def test_concurrent_disconnected_recovery_launches_one_replacement(
        self, manager: BrowserManager
    ) -> None:
        """Test concurrent recovery callers share one replacement browser."""
        stale_browser = MagicMock()
        stale_browser.is_connected.return_value = False
        stale_browser.close = AsyncMock()
        stale_playwright = MagicMock()
        stale_playwright.stop = AsyncMock()
        fresh_browser = MagicMock()
        fresh_browser.is_connected.return_value = True
        fresh_playwright = MagicMock()
        launch_started = asyncio.Event()
        allow_launch = asyncio.Event()

        async def launch(**_kwargs: object) -> MagicMock:
            launch_started.set()
            await allow_launch.wait()
            return fresh_browser

        fresh_playwright.chromium.launch = AsyncMock(side_effect=launch)
        manager._browser = stale_browser
        manager._playwright = stale_playwright

        with patch("app.services.browser.async_playwright") as mock_async_pw:
            mock_async_pw.return_value.start = AsyncMock(return_value=fresh_playwright)
            first = asyncio.create_task(manager._ensure_browser())
            await launch_started.wait()
            second = asyncio.create_task(manager._ensure_browser())
            allow_launch.set()
            await asyncio.gather(first, second)

        mock_async_pw.return_value.start.assert_awaited_once()
        fresh_playwright.chromium.launch.assert_awaited_once()
        stale_browser.close.assert_awaited_once()
        stale_playwright.stop.assert_awaited_once()
        assert manager._browser is fresh_browser

    @pytest.mark.asyncio
    async def test_ensure_browser_relaunches_when_stale_cleanup_fails(
        self, manager: BrowserManager, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Test stale cleanup failures are sanitized and do not prevent relaunch."""
        stale_browser = MagicMock()
        stale_browser.is_connected.return_value = False
        stale_browser.close = AsyncMock(side_effect=RuntimeError("https://user:secret@example.com/close"))
        stale_playwright = MagicMock()
        stale_playwright.stop = AsyncMock(side_effect=RuntimeError("https://user:secret@example.com/stop"))
        fresh_browser = MagicMock()
        fresh_browser.is_connected.return_value = True
        fresh_playwright = MagicMock()
        fresh_playwright.chromium.launch = AsyncMock(return_value=fresh_browser)
        manager._browser = stale_browser
        manager._playwright = stale_playwright

        with (
            patch("app.services.browser.async_playwright") as mock_async_pw,
            caplog.at_level(logging.WARNING),
        ):
            mock_async_pw.return_value.start = AsyncMock(return_value=fresh_playwright)
            await manager._ensure_browser()

        assert manager._browser is fresh_browser
        assert "secret" not in caplog.text
        assert len(caplog.records) == 2

    @pytest.mark.asyncio
    async def test_disconnected_recovery_cancelled_during_launch_cleans_all_resources(
        self, manager: BrowserManager
    ) -> None:
        """Test cancellation during recovery cleans stale and replacement Playwright resources."""
        stale_browser = MagicMock()
        stale_browser.is_connected.return_value = False
        stale_browser.close = AsyncMock()
        stale_playwright = MagicMock()
        stale_playwright.stop = AsyncMock()
        fresh_playwright = MagicMock()
        fresh_playwright.chromium.launch = AsyncMock(side_effect=asyncio.CancelledError())
        fresh_playwright.stop = AsyncMock()
        manager._browser = stale_browser
        manager._playwright = stale_playwright

        with patch("app.services.browser.async_playwright") as mock_async_pw:
            mock_async_pw.return_value.start = AsyncMock(return_value=fresh_playwright)
            with pytest.raises(asyncio.CancelledError):
                await manager._ensure_browser()

        stale_browser.close.assert_awaited_once()
        stale_playwright.stop.assert_awaited_once()
        fresh_playwright.stop.assert_awaited_once()
        assert manager._browser is None
        assert manager._playwright is None

    @pytest.mark.asyncio
    async def test_shutdown_starting_during_stale_cleanup_prevents_relaunch(
        self, manager: BrowserManager
    ) -> None:
        """Test shutdown detected after stale cleanup prevents a replacement launch."""
        stale_browser = MagicMock()
        stale_browser.is_connected.return_value = False

        async def close_and_start_shutdown() -> None:
            manager._shutting_down = True

        stale_browser.close = AsyncMock(side_effect=close_and_start_shutdown)
        stale_playwright = MagicMock()
        stale_playwright.stop = AsyncMock()
        manager._browser = stale_browser
        manager._playwright = stale_playwright

        with patch("app.services.browser.async_playwright") as mock_async_pw:
            with pytest.raises(RuntimeError, match="shutting down"):
                await manager._ensure_browser()

        stale_browser.close.assert_awaited_once()
        stale_playwright.stop.assert_awaited_once()
        mock_async_pw.assert_not_called()
        assert manager._browser is None
        assert manager._playwright is None

    @pytest.mark.asyncio
    async def test_shutdown_starting_during_playwright_start_stops_new_instance(
        self, manager: BrowserManager
    ) -> None:
        """Test shutdown detected after Playwright starts stops it before browser launch."""
        fresh_playwright = MagicMock()
        fresh_playwright.stop = AsyncMock()

        async def start_and_begin_shutdown() -> MagicMock:
            manager._shutting_down = True
            return fresh_playwright

        with patch("app.services.browser.async_playwright") as mock_async_pw:
            mock_async_pw.return_value.start = AsyncMock(side_effect=start_and_begin_shutdown)
            with pytest.raises(RuntimeError, match="shutting down"):
                await manager._ensure_browser()

        fresh_playwright.chromium.launch.assert_not_called()
        fresh_playwright.stop.assert_awaited_once()
        assert manager._browser is None
        assert manager._playwright is None

    @pytest.mark.asyncio
    async def test_disconnected_event_relaunches_and_ignores_old_callback(
        self, manager: BrowserManager, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Test disconnect callback invalidates only its own browser instance."""
        page = AsyncMock()
        first_browser = MagicMock()
        first_browser.is_connected.return_value = True
        second_browser = MagicMock()
        second_browser.is_connected.return_value = True
        second_browser.new_page = AsyncMock(return_value=page)
        first_playwright = MagicMock()
        first_playwright.chromium.launch = AsyncMock(return_value=first_browser)
        first_playwright.stop = AsyncMock()
        second_playwright = MagicMock()
        second_playwright.chromium.launch = AsyncMock(return_value=second_browser)

        with (
            patch("app.services.browser.async_playwright") as mock_async_pw,
            caplog.at_level(logging.WARNING),
        ):
            mock_async_pw.return_value.start = AsyncMock(side_effect=[first_playwright, second_playwright])
            await manager._ensure_browser()
            first_browser.on.assert_called_once_with("disconnected", manager._on_browser_disconnected)
            callback = first_browser.on.call_args.args[1]
            callback(first_browser)
            callback(first_browser)
            result = await manager.create_page({"width": 800, "height": 600, "device_scale_factor": 1})

        assert manager._browser is second_browser
        assert result is page
        assert caplog.text.lower().count("disconnected") == 1
        first_playwright.stop.assert_awaited_once()
        callback(first_browser)
        assert manager._browser is second_browser
        assert manager._browser_disconnected is False
        await manager.release_page(page)

        with patch("app.services.browser.async_playwright") as mock_async_pw:
            next_page = await manager.create_page({"width": 800, "height": 600, "device_scale_factor": 1})

        assert next_page is page
        mock_async_pw.assert_not_called()

    @pytest.mark.asyncio
    async def test_disconnected_event_is_quiet_during_shutdown(
        self, manager: BrowserManager, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Test intentional shutdown disconnect does not log a warning or relaunch."""
        mock_browser = MagicMock()
        mock_browser.is_connected.return_value = True

        async def close_and_disconnect() -> None:
            callback = mock_browser.on.call_args.args[1]
            callback(mock_browser)

        mock_browser.close = AsyncMock(side_effect=close_and_disconnect)
        mock_playwright = MagicMock()
        mock_playwright.chromium.launch = AsyncMock(return_value=mock_browser)
        mock_playwright.stop = AsyncMock()

        with (
            patch("app.services.browser.async_playwright") as mock_async_pw,
            caplog.at_level(logging.WARNING),
        ):
            mock_async_pw.return_value.start = AsyncMock(return_value=mock_playwright)
            await manager._ensure_browser()
            await manager.shutdown()

        assert "disconnected" not in caplog.text.lower()
        assert mock_async_pw.return_value.start.await_count == 1
        assert manager._browser is None

    @pytest.mark.asyncio
    async def test_create_page(self, manager: BrowserManager) -> None:
        """Test create_page."""
        mock_page = MagicMock()
        mock_browser = AsyncMock()
        mock_browser.is_connected = MagicMock(return_value=True)
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

    @pytest.mark.asyncio
    async def test_shutdown_allows_later_reuse(self, manager: BrowserManager) -> None:
        """Test shutdown closes resources without making the manager permanently unusable."""
        mock_browser = AsyncMock()
        mock_playwright = AsyncMock()

        manager._browser = mock_browser
        manager._playwright = mock_playwright

        await manager.shutdown()

        assert manager._shutting_down is False
        manager.configure(logging.getLogger("reused"), proxy_url="http://proxy.example:8080")
        manager._ensure_browser = AsyncMock()
        manager._browser = AsyncMock()
        await manager.create_page({"width": 800, "height": 600, "device_scale_factor": 2})
        manager._ensure_browser.assert_awaited_once()

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
        mock_browser.is_connected.return_value = True

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
        mock_browser.is_connected = MagicMock(return_value=True)
        mock_browser.new_page = AsyncMock(side_effect=RuntimeError("page boom"))
        manager._browser = mock_browser
        manager._ensure_browser = AsyncMock()

        with pytest.raises(RuntimeError, match="page boom"):
            await manager.create_page({"width": 800, "height": 600, "device_scale_factor": 1})

        assert manager._active_pages == 0

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "message",
        [
            "Target page, context or browser has been closed",
            "Browser has been closed",
            "Browser is closed",
            "Browser closed",
            "Connection closed",
            "Connection is closed",
        ],
    )
    async def test_create_page_retries_closed_browser_error_once(
        self, manager: BrowserManager, message: str
    ) -> None:
        """Test closed-browser errors relaunch and retry exactly once."""
        page = MagicMock()
        stale_browser = MagicMock()
        stale_browser.is_connected.return_value = True
        stale_browser.close = AsyncMock()
        stale_browser.new_page = AsyncMock(side_effect=PlaywrightError(message))
        stale_playwright = MagicMock()
        stale_playwright.stop = AsyncMock()
        fresh_browser = MagicMock()
        fresh_browser.is_connected.return_value = True
        fresh_browser.new_page = AsyncMock(return_value=page)
        fresh_playwright = MagicMock()
        fresh_playwright.chromium.launch = AsyncMock(return_value=fresh_browser)
        manager._browser = stale_browser
        manager._playwright = stale_playwright

        with patch("app.services.browser.async_playwright") as mock_async_pw:
            mock_async_pw.return_value.start = AsyncMock(return_value=fresh_playwright)
            result = await manager.create_page({"width": 800, "height": 600, "device_scale_factor": 1})

        assert result is page
        stale_browser.new_page.assert_awaited_once()
        stale_browser.close.assert_awaited_once()
        stale_playwright.stop.assert_awaited_once()
        fresh_playwright.chromium.launch.assert_awaited_once()
        fresh_browser.new_page.assert_awaited_once()
        assert manager._active_pages == 1

    @pytest.mark.asyncio
    async def test_create_page_closed_browser_retry_failure_rolls_back(
        self, manager: BrowserManager
    ) -> None:
        """Test a failed retry propagates and rolls back active page accounting."""
        stale_browser = MagicMock()
        stale_browser.is_connected.return_value = True
        stale_browser.new_page = AsyncMock(side_effect=PlaywrightError("Browser has been closed"))
        fresh_browser = MagicMock()
        fresh_browser.is_connected.return_value = True
        fresh_browser.new_page = AsyncMock(side_effect=PlaywrightError("Target closed"))
        manager._browser = stale_browser

        async def ensure_browser() -> None:
            if stale_browser.new_page.await_count:
                manager._browser = None
                manager._browser = fresh_browser

        manager._ensure_browser = AsyncMock(side_effect=ensure_browser)

        with pytest.raises(PlaywrightError, match="Target closed"):
            await manager.create_page({"width": 800, "height": 600, "device_scale_factor": 1})

        assert manager._ensure_browser.await_count == 2
        fresh_browser.new_page.assert_awaited_once()
        assert manager._active_pages == 0

    @pytest.mark.asyncio
    @pytest.mark.parametrize("message", ["Target closed", "Target has been closed"])
    async def test_create_page_target_closed_preserves_connected_browser(
        self, manager: BrowserManager, message: str
    ) -> None:
        """Test target-only errors preserve a connected browser and other active pages."""
        error = PlaywrightError(message)
        mock_browser = MagicMock()
        mock_browser.is_connected.return_value = True
        mock_browser.new_page = AsyncMock(side_effect=error)
        mock_browser.close = AsyncMock()
        mock_playwright = MagicMock()
        mock_playwright.stop = AsyncMock()
        manager._browser = mock_browser
        manager._playwright = mock_playwright
        manager._active_pages = 1

        with patch("app.services.browser.async_playwright") as mock_async_pw:
            with pytest.raises(PlaywrightError) as exc_info:
                await manager.create_page({"width": 800, "height": 600, "device_scale_factor": 1})

        assert exc_info.value is error
        mock_browser.new_page.assert_awaited_once()
        mock_browser.close.assert_not_awaited()
        mock_playwright.stop.assert_not_awaited()
        mock_async_pw.assert_not_called()
        assert manager._browser is mock_browser
        assert manager._playwright is mock_playwright
        assert manager._browser_disconnected is False
        assert manager._active_pages == 1

    @pytest.mark.asyncio
    async def test_create_page_generic_playwright_error_is_not_retried(
        self, manager: BrowserManager
    ) -> None:
        """Test a generic Playwright error propagates without relaunch."""
        mock_browser = MagicMock()
        mock_browser.is_connected.return_value = True
        mock_browser.new_page = AsyncMock(side_effect=PlaywrightError("Invalid viewport"))
        manager._browser = mock_browser
        manager._ensure_browser = AsyncMock()

        with pytest.raises(PlaywrightError, match="Invalid viewport"):
            await manager.create_page({"width": 800, "height": 600, "device_scale_factor": 1})

        manager._ensure_browser.assert_awaited_once()
        mock_browser.new_page.assert_awaited_once()
        assert manager._active_pages == 0

    @pytest.mark.asyncio
    async def test_create_page_retries_when_browser_disconnects_after_error(
        self, manager: BrowserManager
    ) -> None:
        """Test an error followed by disconnect triggers one relaunch and retry."""
        page = MagicMock()
        stale_browser = MagicMock()
        stale_browser.is_connected.return_value = False
        stale_browser.new_page = AsyncMock(side_effect=RuntimeError("transport failed"))
        fresh_browser = MagicMock()
        fresh_browser.is_connected.return_value = True
        fresh_browser.new_page = AsyncMock(return_value=page)
        manager._browser = stale_browser

        async def ensure_browser() -> None:
            if stale_browser.new_page.await_count:
                manager._browser = None
                manager._browser = fresh_browser

        manager._ensure_browser = AsyncMock(side_effect=ensure_browser)

        result = await manager.create_page({"width": 800, "height": 600, "device_scale_factor": 1})

        assert result is page
        assert manager._ensure_browser.await_count == 2
        assert manager._active_pages == 1

    @pytest.mark.asyncio
    async def test_create_page_cancelled_during_retry_rolls_back(self, manager: BrowserManager) -> None:
        """Test cancellation during retry rolls back active page accounting."""
        stale_browser = MagicMock()
        stale_browser.is_connected.return_value = True
        stale_browser.new_page = AsyncMock(side_effect=PlaywrightError("Browser closed"))
        fresh_browser = MagicMock()
        fresh_browser.is_connected.return_value = True
        fresh_browser.new_page = AsyncMock(side_effect=asyncio.CancelledError())
        manager._browser = stale_browser

        async def ensure_browser() -> None:
            if stale_browser.new_page.await_count:
                manager._browser = None
                manager._browser = fresh_browser

        manager._ensure_browser = AsyncMock(side_effect=ensure_browser)

        with pytest.raises(asyncio.CancelledError):
            await manager.create_page({"width": 800, "height": 600, "device_scale_factor": 1})

        assert manager._active_pages == 0

    @pytest.mark.asyncio
    async def test_create_page_shutdown_starting_during_retry_rolls_back(
        self, manager: BrowserManager
    ) -> None:
        """Test shutdown beginning during recovery prevents retry and rolls back accounting."""
        stale_browser = MagicMock()
        stale_browser.is_connected.return_value = True
        stale_browser.new_page = AsyncMock(side_effect=PlaywrightError("Browser closed"))
        manager._browser = stale_browser
        ensure_calls = 0

        async def ensure_browser() -> None:
            nonlocal ensure_calls
            ensure_calls += 1
            if ensure_calls == 2:
                manager._shutting_down = True
                raise RuntimeError("Browser manager is shutting down")

        manager._ensure_browser = AsyncMock(side_effect=ensure_browser)

        with pytest.raises(RuntimeError, match="shutting down"):
            await manager.create_page({"width": 800, "height": 600, "device_scale_factor": 1})

        assert manager._ensure_browser.await_count == 2
        stale_browser.new_page.assert_awaited_once()
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
