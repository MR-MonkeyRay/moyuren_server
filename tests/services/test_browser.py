"""Tests for app/services/browser.py - browser manager."""

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
