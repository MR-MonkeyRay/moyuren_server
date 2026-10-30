"""app/services/stock_index.py 股票指数服务测试。"""

from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import respx
from httpx import Response

from app.core.config import StockIndexSource
from app.services.compute import DataComputer
from app.services.stock_index import StockIndexService


class TestStockIndexService:
    """StockIndexService 类测试。"""

    @pytest.fixture
    def sample_config(self) -> StockIndexSource:
        """创建股票指数配置样例。"""
        return StockIndexSource(
            quote_url="https://api.example.com/quote",
            secids=[
                {"secid": "1.000001", "name": "上证指数", "market": "A"},
                {"secid": "0.399001", "name": "深证成指", "market": "A"},
            ],
            timeout_sec=5,
            market_timezones={"A": "Asia/Shanghai", "HK": "Asia/Hong_Kong", "US": "America/New_York"},
            cache_ttl_sec=60,
        )

    @pytest.fixture
    def service(self, sample_config: StockIndexSource) -> StockIndexService:
        """创建 StockIndexService 实例。"""
        with patch("app.services.stock_index.xcals.get_calendar") as mock_cal:
            mock_calendar = MagicMock()
            mock_calendar.is_session.return_value = True
            mock_cal.return_value = mock_calendar
            return StockIndexService(config=sample_config)

    @pytest.fixture
    def sample_quote_response(self) -> dict[str, Any]:
        """东方财富 API 响应样例。"""
        return {
            "rc": 0,
            "rt": 17,
            "svr": 181735387,
            "lt": 1,
            "full": 1,
            "data": {
                "total": 2,
                "diff": [
                    {"f2": 3200.50, "f3": 1.25, "f4": 40.00, "f12": "000001", "f14": "上证指数"},
                    {"f2": 10500.00, "f3": -0.50, "f4": -52.00, "f12": "399001", "f14": "深证成指"},
                ],
            },
        }

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_indices_success(self, service: StockIndexService, sample_quote_response: dict) -> None:
        """测试成功获取指数。"""
        respx.get(url__regex=r".*quote.*").mock(return_value=Response(200, json=sample_quote_response))

        now = datetime(2026, 2, 4, 10, 0, 0, tzinfo=timezone(timedelta(hours=8)))
        result = await service.fetch_indices(now)

        assert "items" in result
        assert "updated" in result
        assert result["is_stale"] is False

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_indices_uses_cache(self, service: StockIndexService, sample_quote_response: dict) -> None:
        """测试 TTL 内复用缓存。"""
        respx.get(url__regex=r".*quote.*").mock(return_value=Response(200, json=sample_quote_response))

        now = datetime(2026, 2, 4, 10, 0, 0, tzinfo=timezone(timedelta(hours=8)))

        # 首次获取
        result1 = await service.fetch_indices(now)

        # 30 秒后再次获取，仍在 TTL 内
        now2 = datetime(2026, 2, 4, 10, 0, 30, tzinfo=timezone(timedelta(hours=8)))
        result2 = await service.fetch_indices(now2)

        # 应只发送一次 HTTP 请求
        assert respx.calls.call_count == 1
        assert result1 == result2

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_indices_cache_expired(self, service: StockIndexService, sample_quote_response: dict) -> None:
        """测试 TTL 到期后刷新缓存。"""
        respx.get(url__regex=r".*quote.*").mock(return_value=Response(200, json=sample_quote_response))

        now = datetime(2026, 2, 4, 10, 0, 0, tzinfo=timezone(timedelta(hours=8)))

        # 首次获取
        await service.fetch_indices(now)

        # 2 分钟后再次获取，已超过 TTL
        now2 = datetime(2026, 2, 4, 10, 2, 0, tzinfo=timezone(timedelta(hours=8)))
        await service.fetch_indices(now2)

        # 应发送两次 HTTP 请求
        assert respx.calls.call_count == 2

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_indices_returns_stale_on_error(
        self, service: StockIndexService, sample_quote_response: dict
    ) -> None:
        """测试出错时返回过期缓存。"""
        # 首次获取成功
        respx.get(url__regex=r".*quote.*").mock(return_value=Response(200, json=sample_quote_response))

        now = datetime(2026, 2, 4, 10, 0, 0, tzinfo=timezone(timedelta(hours=8)))
        await service.fetch_indices(now)

        # 清空 mock 并设置失败响应
        respx.reset()
        respx.get(url__regex=r".*quote.*").mock(return_value=Response(502))

        # TTL 到期后再次获取时出错
        now2 = datetime(2026, 2, 4, 10, 2, 0, tzinfo=timezone(timedelta(hours=8)))
        result = await service.fetch_indices(now2)

        assert result["is_stale"] is True

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_indices_returns_placeholder_on_error_no_cache(self, service: StockIndexService) -> None:
        """测试出错且无缓存时返回占位数据。"""
        respx.get(url__regex=r".*quote.*").mock(return_value=Response(502))

        now = datetime(2026, 2, 4, 10, 0, 0, tzinfo=timezone(timedelta(hours=8)))
        result = await service.fetch_indices(now)

        assert "items" in result
        assert result["items"] == []
        assert result["is_data_missing"] is True
        assert result["trading_day"] == {"A": True}
        computed = DataComputer().compute({"stock_indices": result}, now=now)
        assert computed["stock_indices"]["is_data_missing"] is True

    def test_process_quotes_uses_config_order_names_and_markets(self) -> None:
        """仅由配置的指数决定显示顺序、名称和市场。"""
        config = StockIndexSource(
            quote_url="https://api.example.com/quote",
            secids=[
                {"secid": "116.CUSTOM", "name": "自选指数", "market": "HK"},
                {"secid": "1.000001", "name": "", "market": "A"},
                {"secid": "100.HSI", "name": "恒生配置名称", "market": "HK"},
            ],
            market_timezones={"A": "Asia/Shanghai", "HK": "Asia/Hong_Kong"},
        )
        with patch("app.services.stock_index.xcals.get_calendar"):
            service = StockIndexService(config)
        now = datetime(2026, 2, 4, tzinfo=timezone.utc)
        items = service._process_quotes(
            [
                {"f12": "000001", "f14": "API名称", "f2": 100, "f3": 1},
                {"f12": "CUSTOM", "f14": "API名称", "f2": 200, "f3": -1},
                {"f12": "DJIA", "f14": "Unconfigured"},
            ],
            {"HK": False, "A": True},
            now,
        )

        assert [item["code"] for item in items] == ["CUSTOM", "000001", "HSI"]
        assert [item["name"] for item in items] == ["自选指数", "", "恒生配置名称"]
        assert [item["market"] for item in items] == ["HK", "A", "HK"]
        assert [item["is_trading_day"] for item in items] == [False, True, False]
        assert items[2]["price"] is None

    def test_trading_days_only_uses_configured_markets(self) -> None:
        """未知日历按各配置市场当地的工作日判断。"""
        config = StockIndexSource(
            quote_url="https://api.example.com/quote",
            secids=[{"secid": "999.TEST", "name": "Test", "market": "OTHER"}],
            market_timezones={"OTHER": "Asia/Tokyo", "US": "America/New_York"},
        )
        with patch("app.services.stock_index.xcals.get_calendar") as get_calendar:
            service = StockIndexService(config)
        get_calendar.assert_not_called()

        # UTC 的周日此时在东京已是周一。
        monday = datetime(2026, 2, 8, 16, tzinfo=timezone.utc)
        assert service._get_trading_days(monday) == {"OTHER": True}
        assert service._get_trading_days(monday - timedelta(days=1)) == {"OTHER": False}

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_indices_handles_timeout(self, service: StockIndexService) -> None:
        """测试妥善处理超时。"""
        respx.get(url__regex=r".*quote.*").mock(side_effect=httpx.TimeoutException("Timeout"))

        now = datetime(2026, 2, 4, 10, 0, 0, tzinfo=timezone(timedelta(hours=8)))
        result = await service.fetch_indices(now)

        # 应返回占位数据
        assert "items" in result

    def test_get_timezone_valid(self, service: StockIndexService) -> None:
        """测试获取有效市场的时区。"""
        tz = service._get_timezone("A")
        assert str(tz) == "Asia/Shanghai"

    def test_get_timezone_fallback(self, service: StockIndexService) -> None:
        """测试未知市场回退到默认时区。"""
        tz = service._get_timezone("UNKNOWN")
        assert str(tz) == "Asia/Shanghai"  # 默认回退时区

    @pytest.mark.asyncio
    async def test_close_client(self, service: StockIndexService) -> None:
        """测试关闭 HTTP 客户端。"""
        # 创建客户端
        await service._get_http_client()
        assert service._http_client is not None

        # 关闭客户端
        await service.close()
        assert service._http_client is None

    @pytest.mark.asyncio
    async def test_injected_client_is_reused_with_timeout_and_not_closed(
        self, sample_config: StockIndexSource, sample_quote_response: dict[str, Any]
    ) -> None:
        """注入的客户端归调用方管理，请求使用服务配置的超时时间。"""
        response = MagicMock()
        response.json.return_value = sample_quote_response
        client = MagicMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=response)
        client.aclose = AsyncMock()

        with patch("app.services.stock_index.xcals.get_calendar"):
            service = StockIndexService(sample_config, http_client=client)

        result = await service._fetch_quotes()
        await service.close()

        assert result == sample_quote_response["data"]["diff"]
        client.get.assert_awaited_once_with(
            "https://api.example.com/quote?fltt=2&fields=f2,f3,f4,f12,f14&secids=1.000001,0.399001",
            timeout=httpx.Timeout(sample_config.timeout_sec),
        )
        client.aclose.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_close_closes_owned_client(
        self, sample_config: StockIndexSource
    ) -> None:
        """延迟创建的客户端仍由服务管理并关闭。"""
        client = MagicMock(spec=httpx.AsyncClient)
        client.is_closed = False
        client.aclose = AsyncMock()
        with (
            patch("app.services.stock_index.xcals.get_calendar"),
            patch("app.services.stock_index.create_async_client", return_value=client),
        ):
            service = StockIndexService(sample_config)
            assert await service._get_http_client() is client
            await service.close()

        client.aclose.assert_awaited_once_with()
        assert service._http_client is None

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_indices_with_naive_datetime(
        self, service: StockIndexService, sample_quote_response: dict
    ) -> None:
        """测试获取数据时处理不带时区的 datetime。"""
        respx.get(url__regex=r".*quote.*").mock(return_value=Response(200, json=sample_quote_response))

        # 不带时区的 datetime
        now = datetime(2026, 2, 4, 10, 0, 0)
        result = await service.fetch_indices(now)

        assert "items" in result

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_indices_with_none_datetime(
        self, service: StockIndexService, sample_quote_response: dict
    ) -> None:
        """测试 datetime 为 None 时使用当前时间。"""
        respx.get(url__regex=r".*quote.*").mock(return_value=Response(200, json=sample_quote_response))

        result = await service.fetch_indices(None)

        assert "items" in result
        assert "updated" in result
