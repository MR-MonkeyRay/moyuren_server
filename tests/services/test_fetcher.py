"""Tests for app/services/fetcher.py - data fetching service."""

import logging

import httpx
import pytest
import respx
from httpx import Response

from app.core.config import NewsSource
from app.services.fetcher import DataFetcher


class TestDataFetcher:
    """Tests for DataFetcher class."""

    @pytest.fixture
    def sample_endpoint(self) -> NewsSource:
        """Create a sample news source configuration."""
        return NewsSource(url="https://api.example.com/news", timeout_sec=10, params={"force-update": "false"})

    @pytest.fixture
    def fetcher(self, sample_endpoint: NewsSource, logger: logging.Logger) -> DataFetcher:
        """Create a DataFetcher instance."""
        return DataFetcher(source=sample_endpoint, logger=logger)

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_endpoint_success(self, fetcher: DataFetcher, sample_endpoint: NewsSource) -> None:
        """Test successful endpoint fetch."""
        mock_data = {"code": 200, "data": {"news": ["Item 1", "Item 2"]}}
        respx.get(sample_endpoint.url).mock(return_value=Response(200, json=mock_data))

        result = await fetcher.fetch()

        assert result == mock_data
        assert respx.calls.call_count == 1

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_endpoint_timeout(self, fetcher: DataFetcher, sample_endpoint: NewsSource) -> None:
        """Test endpoint fetch timeout returns None."""
        respx.get(sample_endpoint.url).mock(side_effect=httpx.TimeoutException("Timeout"))

        result = await fetcher.fetch()

        assert result is None

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_endpoint_http_error(self, fetcher: DataFetcher, sample_endpoint: NewsSource) -> None:
        """Test endpoint fetch HTTP error returns None."""
        respx.get(sample_endpoint.url).mock(return_value=Response(500))

        result = await fetcher.fetch()

        assert result is None

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_endpoint_request_error(self, fetcher: DataFetcher, sample_endpoint: NewsSource) -> None:
        """Test endpoint fetch request error returns None."""
        respx.get(sample_endpoint.url).mock(side_effect=httpx.ConnectError("Connection failed"))

        result = await fetcher.fetch()

        assert result is None

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_endpoint_invalid_json(self, fetcher: DataFetcher, sample_endpoint: NewsSource) -> None:
        """Test endpoint fetch with invalid JSON returns None."""
        respx.get(sample_endpoint.url).mock(return_value=Response(200, content=b"not json"))

        result = await fetcher.fetch()

        assert result is None

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_all_success(self, logger: logging.Logger) -> None:
        """Test fetch_all returns news result in legacy format."""
        source = NewsSource(url="https://api.example.com/news")
        fetcher = DataFetcher(source=source, logger=logger)

        respx.get("https://api.example.com/news").mock(return_value=Response(200, json={"type": "news"}))

        result = await fetcher.fetch_all()

        assert "news" in result
        assert result["news"]["type"] == "news"

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_all_partial_failure(self, logger: logging.Logger) -> None:
        """Test fetch_all handles fetch failure."""
        source = NewsSource(url="https://api.example.com/news")
        fetcher = DataFetcher(source=source, logger=logger)

        respx.get("https://api.example.com/news").mock(return_value=Response(500))

        result = await fetcher.fetch_all()

        assert result["news"] is None

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_endpoint_with_params(self, logger: logging.Logger) -> None:
        """Test endpoint fetch includes query parameters."""
        source = NewsSource(url="https://api.example.com/news", params={"key": "value", "limit": "10"})
        fetcher = DataFetcher(source=source, logger=logger)

        mock_route = respx.get("https://api.example.com/news").mock(return_value=Response(200, json={"data": "test"}))

        await fetcher.fetch()

        assert mock_route.called
        # Verify params were included in the request
        request = respx.calls.last.request
        assert "key=value" in str(request.url)
        assert "limit=10" in str(request.url)
