"""Tests for app/services/stock_index.py - stock index service."""

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
    """Tests for StockIndexService class."""

    @pytest.fixture
    def sample_config(self) -> StockIndexSource:
        """Create a sample stock index configuration."""
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
        """Create a StockIndexService instance."""
        with patch("app.services.stock_index.xcals.get_calendar") as mock_cal:
            mock_calendar = MagicMock()
            mock_calendar.is_session.return_value = True
            mock_cal.return_value = mock_calendar
            return StockIndexService(config=sample_config)

    @pytest.fixture
    def sample_quote_response(self) -> dict[str, Any]:
        """Sample Eastmoney API response."""
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
        """Test successful index fetch."""
        respx.get(url__regex=r".*quote.*").mock(return_value=Response(200, json=sample_quote_response))

        now = datetime(2026, 2, 4, 10, 0, 0, tzinfo=timezone(timedelta(hours=8)))
        result = await service.fetch_indices(now)

        assert "items" in result
        assert "updated" in result
        assert result["is_stale"] is False

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_indices_uses_cache(self, service: StockIndexService, sample_quote_response: dict) -> None:
        """Test fetch uses cache within TTL."""
        respx.get(url__regex=r".*quote.*").mock(return_value=Response(200, json=sample_quote_response))

        now = datetime(2026, 2, 4, 10, 0, 0, tzinfo=timezone(timedelta(hours=8)))

        # First fetch
        result1 = await service.fetch_indices(now)

        # Second fetch within TTL (30 seconds later)
        now2 = datetime(2026, 2, 4, 10, 0, 30, tzinfo=timezone(timedelta(hours=8)))
        result2 = await service.fetch_indices(now2)

        # Should only make one HTTP request
        assert respx.calls.call_count == 1
        assert result1 == result2

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_indices_cache_expired(self, service: StockIndexService, sample_quote_response: dict) -> None:
        """Test fetch refreshes cache after TTL."""
        respx.get(url__regex=r".*quote.*").mock(return_value=Response(200, json=sample_quote_response))

        now = datetime(2026, 2, 4, 10, 0, 0, tzinfo=timezone(timedelta(hours=8)))

        # First fetch
        await service.fetch_indices(now)

        # Second fetch after TTL (2 minutes later)
        now2 = datetime(2026, 2, 4, 10, 2, 0, tzinfo=timezone(timedelta(hours=8)))
        await service.fetch_indices(now2)

        # Should make two HTTP requests
        assert respx.calls.call_count == 2

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_indices_returns_stale_on_error(
        self, service: StockIndexService, sample_quote_response: dict
    ) -> None:
        """Test returns stale cache on error."""
        # First successful fetch
        respx.get(url__regex=r".*quote.*").mock(return_value=Response(200, json=sample_quote_response))

        now = datetime(2026, 2, 4, 10, 0, 0, tzinfo=timezone(timedelta(hours=8)))
        await service.fetch_indices(now)

        # Clear mock and set up failure
        respx.reset()
        respx.get(url__regex=r".*quote.*").mock(return_value=Response(502))

        # Second fetch after TTL with error
        now2 = datetime(2026, 2, 4, 10, 2, 0, tzinfo=timezone(timedelta(hours=8)))
        result = await service.fetch_indices(now2)

        assert result["is_stale"] is True

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_indices_returns_placeholder_on_error_no_cache(self, service: StockIndexService) -> None:
        """Test returns placeholder when error and no cache."""
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
        """Configured indices alone define display order, names and markets."""
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
        """Unknown calendars fall back to each configured market's local weekday."""
        config = StockIndexSource(
            quote_url="https://api.example.com/quote",
            secids=[{"secid": "999.TEST", "name": "Test", "market": "OTHER"}],
            market_timezones={"OTHER": "Asia/Tokyo", "US": "America/New_York"},
        )
        with patch("app.services.stock_index.xcals.get_calendar") as get_calendar:
            service = StockIndexService(config)
        get_calendar.assert_not_called()

        # Sunday UTC is already Monday in Tokyo.
        monday = datetime(2026, 2, 8, 16, tzinfo=timezone.utc)
        assert service._get_trading_days(monday) == {"OTHER": True}
        assert service._get_trading_days(monday - timedelta(days=1)) == {"OTHER": False}

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_indices_handles_timeout(self, service: StockIndexService) -> None:
        """Test handles timeout gracefully."""
        respx.get(url__regex=r".*quote.*").mock(side_effect=httpx.TimeoutException("Timeout"))

        now = datetime(2026, 2, 4, 10, 0, 0, tzinfo=timezone(timedelta(hours=8)))
        result = await service.fetch_indices(now)

        # Should return placeholder data
        assert "items" in result

    def test_get_timezone_valid(self, service: StockIndexService) -> None:
        """Test get timezone with valid market."""
        tz = service._get_timezone("A")
        assert str(tz) == "Asia/Shanghai"

    def test_get_timezone_fallback(self, service: StockIndexService) -> None:
        """Test get timezone fallback for unknown market."""
        tz = service._get_timezone("UNKNOWN")
        assert str(tz) == "Asia/Shanghai"  # Default fallback

    @pytest.mark.asyncio
    async def test_close_client(self, service: StockIndexService) -> None:
        """Test HTTP client close."""
        # Create client
        await service._get_http_client()
        assert service._http_client is not None

        # Close client
        await service.close()
        assert service._http_client is None

    @pytest.mark.asyncio
    async def test_injected_client_is_reused_with_timeout_and_not_closed(
        self, sample_config: StockIndexSource, sample_quote_response: dict[str, Any]
    ) -> None:
        """An injected client is caller-owned and receives the service timeout."""
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
        """A lazily created client remains owned and is closed by the service."""
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
        """Test fetch handles naive datetime."""
        respx.get(url__regex=r".*quote.*").mock(return_value=Response(200, json=sample_quote_response))

        # Naive datetime (no timezone)
        now = datetime(2026, 2, 4, 10, 0, 0)
        result = await service.fetch_indices(now)

        assert "items" in result

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_indices_with_none_datetime(
        self, service: StockIndexService, sample_quote_response: dict
    ) -> None:
        """Test fetch with None datetime uses current time."""
        respx.get(url__regex=r".*quote.*").mock(return_value=Response(200, json=sample_quote_response))

        result = await service.fetch_indices(None)

        assert "items" in result
        assert "updated" in result
