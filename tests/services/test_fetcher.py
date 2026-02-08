"""Tests for app/services/fetcher.py - data fetching service."""

import logging
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx
from httpx import Response

from app.core.config import FetchEndpointConfig
from app.services.fetcher import DataFetcher


class TestDataFetcher:
    """Tests for DataFetcher class."""

    @pytest.fixture
    def sample_endpoint(self) -> FetchEndpointConfig:
        """Create a sample endpoint configuration."""
        return FetchEndpointConfig(
            name="news",
            url="https://api.example.com/news",
            timeout_sec=10,
            params={"force-update": "false"}
        )

    @pytest.fixture
    def fetcher(self, sample_endpoint: FetchEndpointConfig, logger: logging.Logger) -> DataFetcher:
        """Create a DataFetcher instance."""
        return DataFetcher(endpoints=[sample_endpoint], logger=logger)

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_endpoint_success(
        self, fetcher: DataFetcher, sample_endpoint: FetchEndpointConfig
    ) -> None:
        """Test successful endpoint fetch."""
        mock_data = {"code": 200, "data": {"news": ["Item 1", "Item 2"]}}
        respx.get(sample_endpoint.url).mock(return_value=Response(200, json=mock_data))

        result = await fetcher.fetch_endpoint(sample_endpoint)

        assert result == mock_data
        assert respx.calls.call_count == 1

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_endpoint_timeout(
        self, fetcher: DataFetcher, sample_endpoint: FetchEndpointConfig
    ) -> None:
        """Test endpoint fetch timeout returns None."""
        respx.get(sample_endpoint.url).mock(side_effect=httpx.TimeoutException("Timeout"))

        result = await fetcher.fetch_endpoint(sample_endpoint)

        assert result is None

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_endpoint_http_error(
        self, fetcher: DataFetcher, sample_endpoint: FetchEndpointConfig
    ) -> None:
        """Test endpoint fetch HTTP error returns None."""
        respx.get(sample_endpoint.url).mock(return_value=Response(500))

        result = await fetcher.fetch_endpoint(sample_endpoint)

        assert result is None

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_endpoint_request_error(
        self, fetcher: DataFetcher, sample_endpoint: FetchEndpointConfig
    ) -> None:
        """Test endpoint fetch request error returns None."""
        respx.get(sample_endpoint.url).mock(side_effect=httpx.ConnectError("Connection failed"))

        result = await fetcher.fetch_endpoint(sample_endpoint)

        assert result is None

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_endpoint_invalid_json(
        self, fetcher: DataFetcher, sample_endpoint: FetchEndpointConfig
    ) -> None:
        """Test endpoint fetch with invalid JSON returns None."""
        respx.get(sample_endpoint.url).mock(return_value=Response(200, content=b"not json"))

        result = await fetcher.fetch_endpoint(sample_endpoint)

        assert result is None

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_all_success(self, logger: logging.Logger) -> None:
        """Test fetch_all returns all endpoint results."""
        endpoints = [
            FetchEndpointConfig(name="news", url="https://api.example.com/news"),
            FetchEndpointConfig(name="weather", url="https://api.example.com/weather"),
        ]
        fetcher = DataFetcher(endpoints=endpoints, logger=logger)

        respx.get("https://api.example.com/news").mock(
            return_value=Response(200, json={"type": "news"})
        )
        respx.get("https://api.example.com/weather").mock(
            return_value=Response(200, json={"type": "weather"})
        )

        result = await fetcher.fetch_all()

        assert "news" in result
        assert "weather" in result
        assert result["news"]["type"] == "news"
        assert result["weather"]["type"] == "weather"

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_all_partial_failure(self, logger: logging.Logger) -> None:
        """Test fetch_all handles partial failures."""
        endpoints = [
            FetchEndpointConfig(name="news", url="https://api.example.com/news"),
            FetchEndpointConfig(name="weather", url="https://api.example.com/weather"),
        ]
        fetcher = DataFetcher(endpoints=endpoints, logger=logger)

        respx.get("https://api.example.com/news").mock(
            return_value=Response(200, json={"type": "news"})
        )
        respx.get("https://api.example.com/weather").mock(return_value=Response(500))

        result = await fetcher.fetch_all()

        assert result["news"]["type"] == "news"
        assert result["weather"] is None

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_endpoint_with_params(self, logger: logging.Logger) -> None:
        """Test endpoint fetch includes query parameters."""
        endpoint = FetchEndpointConfig(
            name="news",
            url="https://api.example.com/news",
            params={"page": "1", "limit": "10"}
        )
        fetcher = DataFetcher(endpoints=[endpoint], logger=logger)

        route = respx.get("https://api.example.com/news").mock(
            return_value=Response(200, json={"data": []})
        )

        await fetcher.fetch_endpoint(endpoint)

        assert route.called
        # Check that params were included in the request
        request = route.calls[0].request
        assert "page=1" in str(request.url)
        assert "limit=10" in str(request.url)

    @pytest.mark.asyncio
    async def test_fetch_all_raises_on_result_length_mismatch(
        self, logger: logging.Logger
    ) -> None:
        """Test fetch_all raises ValueError when gather returns mismatched length."""
        endpoints = [
            FetchEndpointConfig(name="news", url="https://api.example.com/news"),
            FetchEndpointConfig(name="weather", url="https://api.example.com/weather"),
        ]
        fetcher = DataFetcher(endpoints=endpoints, logger=logger)

        async def fake_gather(*coros_or_futures):
            # Consume all coroutines to avoid RuntimeWarning
            for coro in coros_or_futures:
                try:
                    await coro
                except Exception:
                    pass
            # Return fewer results than endpoints to trigger zip(strict=True)
            return [{"type": "news"}]

        with patch.object(fetcher, "fetch_endpoint", new=AsyncMock(return_value={"ok": True})):
            with patch("app.services.fetcher.asyncio.gather", side_effect=fake_gather):
                with pytest.raises(ValueError, match=r"zip\(\)"):
                    await fetcher.fetch_all()
