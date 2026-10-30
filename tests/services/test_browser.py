"""app/services/browser.py 浏览器管理器测试。"""

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from playwright.async_api import Error as PlaywrightError

from app.services.browser import BrowserManager


class TestBrowserManager:
    """BrowserManager 类测试。"""

    @pytest.fixture
    def manager(self, logger: logging.Logger) -> BrowserManager:
        """创建 BrowserManager 实例。"""
        return BrowserManager(logger_instance=logger)

    def test_init(self, manager: BrowserManager) -> None:
        """测试浏览器管理器初始化。"""
        assert manager._browser is None
        assert manager._playwright is None
        assert manager.is_ready is False

    def test_is_ready_with_connected_browser(self, manager: BrowserManager) -> None:
        """测试就绪检查识别已连接的浏览器，且不启动新浏览器。"""
        manager._browser = MagicMock()
        manager._browser.is_connected.return_value = True

        assert manager.is_ready is True
        manager._browser.is_connected.assert_called_once_with()

    def test_is_ready_with_disconnected_browser(self, manager: BrowserManager) -> None:
        """测试已断连的浏览器被判定为未就绪。"""
        manager._browser = MagicMock()
        manager._browser.is_connected.return_value = False

        assert manager.is_ready is False

    def test_configure(self, manager: BrowserManager) -> None:
        """测试配置日志记录器。"""
        new_logger = logging.getLogger("new_test")
        manager.configure(new_logger, proxy_url="http://proxy.example:8080")
        assert manager._logger is new_logger
        assert manager._proxy_url == "http://proxy.example:8080"

    def test_configure_rejects_proxy_change_after_launch(self, manager: BrowserManager) -> None:
        """测试浏览器启动后不允许更改代理。"""
        manager._browser = MagicMock()

        with pytest.raises(RuntimeError, match="Cannot change browser proxy"):
            manager.configure(logging.getLogger("new_test"), proxy_url="http://proxy.example:8080")

    @pytest.mark.asyncio
    async def test_ensure_browser_lazy_init(self, manager: BrowserManager) -> None:
        """测试 _ensure_browser 延迟初始化。"""
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
        """测试 Chromium 启动时接收 Playwright 代理配置。"""
        manager.configure(logging.getLogger("new_test"), proxy_url="http://user:pass@proxy.example:8080")
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
                "server": "http://proxy.example:8080",
                "username": "user",
                "password": "pass",
            },
        )

    @pytest.mark.asyncio
    async def test_ensure_browser_rejects_authenticated_socks_proxy(
        self, manager: BrowserManager
    ) -> None:
        """测试在浏览器启动前拒绝 Chromium 不支持的 SOCKS 认证。"""
        manager.configure(logging.getLogger("new_test"), proxy_url="socks5://user:pass@proxy.example:1080")
        mock_playwright_instance = AsyncMock()

        with patch("app.services.browser.async_playwright") as mock_async_pw:
            mock_async_pw.return_value.start = AsyncMock(return_value=mock_playwright_instance)

            with pytest.raises(ValueError, match="Chromium cannot authenticate to SOCKS"):
                await manager._ensure_browser()

        mock_playwright_instance.chromium.launch.assert_not_called()
        mock_playwright_instance.stop.assert_awaited_once()
        assert manager._browser is None
        assert manager._playwright is None

    @pytest.mark.asyncio
    async def test_ensure_browser_already_initialized(self, manager: BrowserManager) -> None:
        """测试已初始化时的 _ensure_browser 行为。"""
        manager._browser = MagicMock()
        manager._browser.is_connected.return_value = True

        with patch("app.services.browser.async_playwright") as mock_async_pw:
            await manager._ensure_browser()

            # 不应调用 async_playwright
            mock_async_pw.assert_not_called()

    @pytest.mark.asyncio
    async def test_ensure_browser_relaunches_disconnected_browser(self, manager: BrowserManager) -> None:
        """测试清理并重新启动已断连的浏览器。"""
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
        """测试并发恢复请求共用同一个新浏览器。"""
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
        """测试旧资源清理失败的信息会脱敏，且不阻止重新启动。"""
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
        """测试恢复过程取消时清理旧资源及新建的 Playwright 资源。"""
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
        """测试清理旧资源后检测到关闭状态时不再启动新浏览器。"""
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
        """测试 Playwright 启动后检测到关闭状态时，先停止它，不启动浏览器。"""
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
        """测试断连回调只将其所属的浏览器实例标记为失效。"""
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
        """测试主动关闭导致的断连不记录警告，也不重新启动。"""
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
        """测试 create_page 创建页面。"""
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
        """测试浏览器未初始化时 create_page 抛出异常。"""
        # mock _ensure_browser，避免实际初始化
        manager._ensure_browser = AsyncMock()
        manager._browser = None

        viewport = {"width": 800, "height": 600, "device_scale_factor": 2}

        with pytest.raises(RuntimeError, match="Browser is not initialized"):
            await manager.create_page(viewport)

    @pytest.mark.asyncio
    async def test_shutdown(self, manager: BrowserManager) -> None:
        """测试关闭流程。"""
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
        """测试关闭流程处理浏览器关闭异常。"""
        mock_browser = AsyncMock()
        mock_browser.close = AsyncMock(side_effect=Exception("Close failed"))
        mock_playwright = AsyncMock()

        manager._browser = mock_browser
        manager._playwright = mock_playwright

        # 不应抛出异常
        await manager.shutdown()

        assert manager._browser is None
        assert manager._playwright is None

    @pytest.mark.asyncio
    async def test_shutdown_handles_stop_error(self, manager: BrowserManager) -> None:
        """测试关闭流程处理 Playwright 停止异常。"""
        mock_browser = AsyncMock()
        mock_playwright = AsyncMock()
        mock_playwright.stop = AsyncMock(side_effect=Exception("Stop failed"))

        manager._browser = mock_browser
        manager._playwright = mock_playwright

        # 不应抛出异常
        await manager.shutdown()

        assert manager._browser is None
        assert manager._playwright is None

    @pytest.mark.asyncio
    async def test_shutdown_when_not_initialized(self, manager: BrowserManager) -> None:
        """测试未初始化时的关闭流程。"""
        # 不应抛出异常
        await manager.shutdown()

        assert manager._browser is None
        assert manager._playwright is None

    @pytest.mark.asyncio
    async def test_shutdown_allows_later_reuse(self, manager: BrowserManager) -> None:
        """测试关闭流程释放资源后，管理器仍可再次使用。"""
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

    # --- 预热 ---

    @pytest.mark.asyncio
    async def test_warmup(self, manager: BrowserManager) -> None:
        """测试预热委托给 _ensure_browser。"""
        manager._ensure_browser = AsyncMock()
        await manager.warmup()
        manager._ensure_browser.assert_awaited_once()

    # --- _ensure_browser：关闭状态检查 ---

    @pytest.mark.asyncio
    async def test_ensure_browser_shutting_down_before_lock(self, manager: BrowserManager) -> None:
        """测试 _ensure_browser 在关闭状态下通过快速路径抛出异常。"""
        manager._shutting_down = True
        with pytest.raises(RuntimeError, match="shutting down"):
            await manager._ensure_browser()

    @pytest.mark.asyncio
    async def test_ensure_browser_shutting_down_inside_lock(self, manager: BrowserManager) -> None:
        """测试 _ensure_browser 在锁内检测到关闭状态时抛出异常。"""

        class _FakeCtx:
            async def __aenter__(self_ctx):
                # 模拟等待锁时另一协程设置关闭状态
                manager._shutting_down = True
                return self_ctx

            async def __aexit__(self_ctx, *args):
                pass

        manager._lock = _FakeCtx()  # type: ignore[assignment]

        with pytest.raises(RuntimeError, match="shutting down"):
            await manager._ensure_browser()

    # --- _ensure_browser：start() 失败 ---

    @pytest.mark.asyncio
    async def test_ensure_browser_start_failure(self, manager: BrowserManager) -> None:
        """测试 async_playwright().start() 本身失败时的 _ensure_browser 行为。"""
        with patch("app.services.browser.async_playwright") as mock_async_pw:
            mock_async_pw.return_value.start = AsyncMock(side_effect=RuntimeError("start boom"))

            with pytest.raises(RuntimeError, match="start boom"):
                await manager._ensure_browser()

            assert manager._browser is None
            assert manager._playwright is None

    # --- _ensure_browser：启动失败后的清理 ---

    @pytest.mark.asyncio
    async def test_ensure_browser_launch_failure_cleans_up_pw(self, manager: BrowserManager) -> None:
        """测试 chromium.launch 失败时 _ensure_browser 清理 Playwright。"""
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
        """测试 _ensure_browser 处理启动失败后的 pw.stop() 异常。"""
        mock_pw = AsyncMock()
        mock_pw.chromium.launch = AsyncMock(side_effect=RuntimeError("launch boom"))
        mock_pw.stop = AsyncMock(side_effect=Exception("stop boom"))

        with patch("app.services.browser.async_playwright") as mock_async_pw:
            mock_async_pw.return_value.start = AsyncMock(return_value=mock_pw)

            with pytest.raises(RuntimeError, match="launch boom"):
                await manager._ensure_browser()

            assert manager._browser is None
            assert manager._playwright is None

    # --- _ensure_browser：取消异常（CancelledError） ---

    @pytest.mark.asyncio
    async def test_ensure_browser_cancelled_cleans_up_pw(self, manager: BrowserManager) -> None:
        """测试 _ensure_browser 遇到 CancelledError 时清理 Playwright。"""
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
        """测试 _ensure_browser 处理取消后的 pw.stop() 异常。"""
        mock_pw = AsyncMock()
        mock_pw.chromium.launch = AsyncMock(side_effect=asyncio.CancelledError())
        mock_pw.stop = AsyncMock(side_effect=Exception("stop boom"))

        with patch("app.services.browser.async_playwright") as mock_async_pw:
            mock_async_pw.return_value.start = AsyncMock(return_value=mock_pw)

            with pytest.raises(asyncio.CancelledError):
                await manager._ensure_browser()

            assert manager._browser is None
            assert manager._playwright is None

    # --- _ensure_browser：锁内二次检查 ---

    @pytest.mark.asyncio
    async def test_ensure_browser_already_initialized_inside_lock(self, manager: BrowserManager) -> None:
        """测试锁内发现浏览器已由另一协程初始化时，_ensure_browser 跳过启动。"""
        mock_browser = MagicMock()
        mock_browser.is_connected.return_value = True

        class _FakeCtx:
            async def __aenter__(self_ctx):
                # 模拟等待锁时另一协程已初始化浏览器
                manager._browser = mock_browser
                return self_ctx

            async def __aexit__(self_ctx, *args):
                pass

        manager._lock = _FakeCtx()  # type: ignore[assignment]

        with patch("app.services.browser.async_playwright") as mock_async_pw:
            await manager._ensure_browser()
            # 浏览器已在锁内设置，不应调用 async_playwright
            mock_async_pw.assert_not_called()

    # --- create_page：关闭状态检查 ---

    @pytest.mark.asyncio
    async def test_create_page_shutting_down_before_ensure(self, manager: BrowserManager) -> None:
        """测试 create_page 在关闭状态下通过快速路径抛出异常。"""
        manager._shutting_down = True
        with pytest.raises(RuntimeError, match="shutting down"):
            await manager.create_page({"width": 800, "height": 600, "device_scale_factor": 1})

    @pytest.mark.asyncio
    async def test_create_page_shutting_down_inside_lock(self, manager: BrowserManager) -> None:
        """测试 create_page 在锁内检测到关闭状态时抛出异常。"""
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

    # --- create_page：异常回滚 ---

    @pytest.mark.asyncio
    async def test_create_page_new_page_error_decrements_counter(self, manager: BrowserManager) -> None:
        """测试 new_page 失败时 create_page 递减 _active_pages。"""
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
        """测试浏览器关闭异常会触发重新启动，且只重试一次。"""
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
        """测试重试失败时向外抛出异常，并回滚活动页面计数。"""
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
        """测试仅涉及目标的异常不会影响已连接的浏览器和其他活动页面。"""
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
        """测试普通 Playwright 异常直接向外抛出，不重新启动浏览器。"""
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
        """测试异常后浏览器断连会触发一次重新启动与重试。"""
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
        """测试重试时取消会回滚活动页面计数。"""
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
        """测试恢复期间开始关闭会阻止重试，并回滚页面计数。"""
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
        """测试 create_page 遇到 CancelledError 时递减 _active_pages。"""
        mock_browser = AsyncMock()
        mock_browser.new_page = AsyncMock(side_effect=asyncio.CancelledError())
        manager._browser = mock_browser
        manager._ensure_browser = AsyncMock()

        with pytest.raises(asyncio.CancelledError):
            await manager.create_page({"width": 800, "height": 600, "device_scale_factor": 1})

        assert manager._active_pages == 0

    # --- 释放页面 ---

    @pytest.mark.asyncio
    async def test_release_page(self, manager: BrowserManager) -> None:
        """测试 release_page 关闭页面并递减计数。"""
        mock_page = AsyncMock()
        manager._active_pages = 1

        await manager.release_page(mock_page)

        mock_page.close.assert_awaited_once()
        assert manager._active_pages == 0

    @pytest.mark.asyncio
    async def test_release_page_close_error(self, manager: BrowserManager) -> None:
        """测试页面关闭失败时 release_page 仍会递减计数。"""
        mock_page = AsyncMock()
        mock_page.close = AsyncMock(side_effect=Exception("close boom"))
        manager._active_pages = 1

        await manager.release_page(mock_page)

        assert manager._active_pages == 0

    @pytest.mark.asyncio
    async def test_release_page_counter_never_negative(self, manager: BrowserManager) -> None:
        """测试 release_page 将计数下限限制为 0。"""
        mock_page = AsyncMock()
        manager._active_pages = 0

        await manager.release_page(mock_page)

        assert manager._active_pages == 0

    # --- shutdown：等待活动页面超时 ---

    @pytest.mark.asyncio
    async def test_shutdown_waits_for_active_pages(self, manager: BrowserManager) -> None:
        """测试关闭流程等待活动页面释放后再关闭资源。"""
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
        """测试超时后即使仍有活动页面也继续关闭。"""
        mock_browser = AsyncMock()
        mock_playwright = AsyncMock()
        manager._browser = mock_browser
        manager._playwright = mock_playwright
        manager._active_pages = 1

        # 将超时时间设得很短
        with patch("app.services.browser._SHUTDOWN_TIMEOUT", 0.05), \
             patch("app.services.browser._SHUTDOWN_POLL_INTERVAL", 0.01):
            await manager.shutdown()

        mock_browser.close.assert_awaited_once()
        assert manager._browser is None
        assert manager._active_pages == 0

    # --- 模块级单例 ---

    def test_module_level_browser_manager_exists(self) -> None:
        """测试模块级 browser_manager 单例已创建。"""
        from app.services.browser import browser_manager

        assert isinstance(browser_manager, BrowserManager)
