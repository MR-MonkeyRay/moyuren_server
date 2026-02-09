"""Tests for app/services/stock_index.py - stock index service."""

from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest
import respx
from httpx import Response

from app.core.config import StockIndexSource
from app.services.stock_index import StockIndexService


class TestStockIndexService:
    """Tests for StockIndexService class."""

    @pytest.fixture
    def sample_config(self) -> StockIndexSource:
        """Create a sample stock index configuration."""
        return StockIndexSource(
            quote_url="https://api.example.com/quote",
            secids=["1.000001", "0.399001"],
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
        respx.get(url__regex=r".*quote.*").mock(return_value=Response(500))

        # Second fetch after TTL with error
        now2 = datetime(2026, 2, 4, 10, 2, 0, tzinfo=timezone(timedelta(hours=8)))
        result = await service.fetch_indices(now2)

        assert result["is_stale"] is True

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_indices_returns_placeholder_on_error_no_cache(self, service: StockIndexService) -> None:
        """Test returns placeholder when error and no cache."""
        respx.get(url__regex=r".*quote.*").mock(return_value=Response(500))

        now = datetime(2026, 2, 4, 10, 0, 0, tzinfo=timezone(timedelta(hours=8)))
        result = await service.fetch_indices(now)

        assert "items" in result
        assert result["items"] == []

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
