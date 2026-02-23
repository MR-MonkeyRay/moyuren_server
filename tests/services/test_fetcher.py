"""Tests for app/services/fetcher.py - data fetching service."""

import logging
from datetime import date
from pathlib import Path

import httpx
import pytest
import respx
from httpx import Response

from app.core.config import NewsSource
from app.services.fetcher import CachedDataFetcher, DataFetcher


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


class TestCachedDataFetcher:
    """Tests for CachedDataFetcher class."""

    @staticmethod
    def _make_news_data(date_str: str) -> dict:
        """Helper function to construct standard API response data."""
        return {"news": {"code": 200, "data": {"date": date_str, "news": ["item1"]}}}

    @pytest.fixture
    def sample_source(self) -> NewsSource:
        """Create a sample news source configuration."""
        return NewsSource(url="https://api.example.com/news")

    @pytest.fixture
    def cached_fetcher(
        self, sample_source: NewsSource, logger: logging.Logger, tmp_path: Path
    ) -> CachedDataFetcher:
        """Create a CachedDataFetcher instance with fixed date."""
        return CachedDataFetcher(
            source=sample_source,
            logger=logger,
            cache_dir=tmp_path,
            date_provider=lambda: date(2026, 2, 23),
        )

    # Tests for _extract_news_date

    def test_extract_news_date_iso_format(
        self, cached_fetcher: CachedDataFetcher
    ) -> None:
        """Test _extract_news_date parses ISO format (YYYY-MM-DD)."""
        data = self._make_news_data("2026-02-23")
        result = cached_fetcher._extract_news_date(data)
        assert result == date(2026, 2, 23)

    def test_extract_news_date_slash_format(
        self, cached_fetcher: CachedDataFetcher
    ) -> None:
        """Test _extract_news_date parses slash format (YYYY/MM/DD)."""
        data = self._make_news_data("2026/02/23")
        result = cached_fetcher._extract_news_date(data)
        assert result == date(2026, 2, 23)

    def test_extract_news_date_chinese_format(
        self, cached_fetcher: CachedDataFetcher
    ) -> None:
        """Test _extract_news_date parses Chinese format (YYYY年M月D日)."""
        data = self._make_news_data("2026年2月4日")
        result = cached_fetcher._extract_news_date(data)
        assert result == date(2026, 2, 4)

    def test_extract_news_date_none_data(
        self, cached_fetcher: CachedDataFetcher
    ) -> None:
        """Test _extract_news_date returns None for None input."""
        result = cached_fetcher._extract_news_date(None)
        assert result is None

    def test_extract_news_date_invalid_format(
        self, cached_fetcher: CachedDataFetcher
    ) -> None:
        """Test _extract_news_date returns None for invalid format."""
        data = {"news": {"data": {"date": "invalid"}}}
        result = cached_fetcher._extract_news_date(data)
        assert result is None

    # Tests for get() method

    @respx.mock
    @pytest.mark.asyncio
    async def test_get_cache_hit_today(
        self, cached_fetcher: CachedDataFetcher, sample_source: NewsSource
    ) -> None:
        """Test get() returns cache when cached news date matches today."""
        # Save cache with today's date
        today_data = self._make_news_data("2026-02-23")
        cached_fetcher.save_cache(today_data)

        # Mock API (should not be called)
        respx.get(sample_source.url).mock(return_value=Response(200, json={"should": "not be called"}))

        result = await cached_fetcher.get()

        assert result == today_data
        assert respx.calls.call_count == 0  # No HTTP request

    @respx.mock
    @pytest.mark.asyncio
    async def test_get_api_date_matches_today(
        self, cached_fetcher: CachedDataFetcher, sample_source: NewsSource
    ) -> None:
        """Test get() saves and returns API data when API date matches today."""
        # No cache
        today_data = self._make_news_data("2026-02-23")
        respx.get(sample_source.url).mock(return_value=Response(200, json=today_data["news"]))

        result = await cached_fetcher.get()

        assert result == today_data
        # Verify cache was saved
        cached_data = cached_fetcher.load_cache()
        assert cached_data == today_data

    @respx.mock
    @pytest.mark.asyncio
    async def test_get_api_not_updated_keep_cache(
        self, cached_fetcher: CachedDataFetcher, sample_source: NewsSource
    ) -> None:
        """Test get() keeps local cache when API returns stale date."""
        # Save cache with yesterday's date
        yesterday_data = self._make_news_data("2026-02-22")
        cached_fetcher.save_cache(yesterday_data)

        # Mock API returns yesterday's date (not updated yet)
        api_response = {"code": 200, "data": {"date": "2026-02-22", "news": ["new item"]}}
        respx.get(sample_source.url).mock(return_value=Response(200, json=api_response))

        result = await cached_fetcher.get()

        # Should return local cache, not API data
        assert result == yesterday_data
        assert result["news"]["data"]["news"] == ["item1"]  # Original cache

    @respx.mock
    @pytest.mark.asyncio
    async def test_get_api_not_updated_no_cache(
        self, cached_fetcher: CachedDataFetcher, sample_source: NewsSource
    ) -> None:
        """Test get() saves API data when no cache and API returns stale date."""
        # No cache
        yesterday_data = self._make_news_data("2026-02-22")
        respx.get(sample_source.url).mock(return_value=Response(200, json=yesterday_data["news"]))

        result = await cached_fetcher.get()

        # Should save and return API data (better than nothing)
        assert result == yesterday_data
        cached_data = cached_fetcher.load_cache()
        assert cached_data == yesterday_data

    @respx.mock
    @pytest.mark.asyncio
    async def test_get_api_failure_fallback(
        self, cached_fetcher: CachedDataFetcher, sample_source: NewsSource
    ) -> None:
        """Test get() returns stale cache when API fails."""
        # Save cache with yesterday's date
        yesterday_data = self._make_news_data("2026-02-22")
        cached_fetcher.save_cache(yesterday_data)

        # Mock API returns 500 error
        respx.get(sample_source.url).mock(return_value=Response(500))

        result = await cached_fetcher.get()

        # Should return stale cache as fallback
        assert result == yesterday_data

    @respx.mock
    @pytest.mark.asyncio
    async def test_get_force_refresh(
        self, cached_fetcher: CachedDataFetcher, sample_source: NewsSource
    ) -> None:
        """Test get(force_refresh=True) calls API and saves new data."""
        # Save cache with yesterday's date
        yesterday_data = self._make_news_data("2026-02-22")
        cached_fetcher.save_cache(yesterday_data)

        # Mock API returns today's date
        today_data = self._make_news_data("2026-02-23")
        respx.get(sample_source.url).mock(return_value=Response(200, json=today_data["news"]))

        result = await cached_fetcher.get(force_refresh=True)

        # Should call API and return new data
        assert result == today_data
        assert respx.calls.call_count == 1
        # Verify cache was updated
        cached_data = cached_fetcher.load_cache()
        assert cached_data == today_data

