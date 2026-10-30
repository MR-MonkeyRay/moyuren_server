"""Tests for app/services/fetcher.py - data fetching service."""

import asyncio
import json
import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterator
from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest
import respx
from httpx import Response

from app.core.config import InstancesConfig, NewsSource
from app.services.calendar import today_business
from app.services.fetcher import (
    _NEWS_STATIC_CDN_URLS,
    _NEWS_STATIC_RAW_PATH,
    CachedDataFetcher,
    DataFetcher,
)


def mock_mirrors_not_found() -> None:
    """Mock every static news mirror URL (today and yesterday) as HTTP 404."""
    today = today_business()
    for target in (today, today - timedelta(days=1)):
        for template in _NEWS_STATIC_CDN_URLS:
            respx.get(template.format(date=target.isoformat())).mock(
                return_value=Response(404)
            )


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

    @pytest.mark.parametrize(
        "payload",
        [
            None,
            [],
            "news",
            {},
            {"code": 500, "message": "err", "data": None},
            {"code": 500, "data": {"news": ["item"]}},
            {"code": "200", "data": {"news": ["item"]}},
            {"code": 200.0, "data": {"news": ["item"]}},
            {"code": None, "data": {"news": ["item"]}},
            {"code": 200, "data": None},
            {"code": 200, "data": []},
            {"code": 200, "data": {"news": []}},
            {"code": 200, "data": {"news": None}},
            {"code": 200, "data": {"news": "item"}},
            {"data": {"news": []}},
        ],
    )
    def test_parse_news_rejects_unusable_payloads(
        self, fetcher: DataFetcher, payload: Any
    ) -> None:
        """错误信封与不含非空新闻列表的响应不可用。"""
        assert fetcher._parse_news_response(payload) is None

    @respx.mock
    @pytest.mark.asyncio
    @pytest.mark.parametrize("include_code", [True, False])
    async def test_fetch_accepts_fixture_envelope(
        self, fetcher: DataFetcher, sample_endpoint: NewsSource, include_code: bool
    ) -> None:
        """现有新闻夹具与省略状态码的兼容信封均可获取。"""
        fixture_path = Path(__file__).parents[1] / "data/news/60s_response.json"
        payload = json.loads(fixture_path.read_text(encoding="utf-8"))
        if not include_code:
            payload.pop("code")
        respx.get(sample_endpoint.url).mock(return_value=Response(200, json=payload))

        assert await fetcher.fetch() == payload
        assert respx.calls.call_count == 1

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
        """Test endpoint fetch timeout returns None when mirrors also fail."""
        respx.get(sample_endpoint.url).mock(side_effect=httpx.TimeoutException("Timeout"))
        mock_mirrors_not_found()

        result = await fetcher.fetch()

        assert result is None

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_endpoint_http_error(self, fetcher: DataFetcher, sample_endpoint: NewsSource) -> None:
        """Test endpoint fetch HTTP error returns None when mirrors also fail."""
        respx.get(sample_endpoint.url).mock(return_value=Response(500))
        mock_mirrors_not_found()

        result = await fetcher.fetch()

        assert result is None

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_endpoint_request_error(self, fetcher: DataFetcher, sample_endpoint: NewsSource) -> None:
        """Test endpoint fetch request error returns None when mirrors also fail."""
        respx.get(sample_endpoint.url).mock(side_effect=httpx.ConnectError("Connection failed"))
        mock_mirrors_not_found()

        result = await fetcher.fetch()

        assert result is None

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_endpoint_invalid_json(self, fetcher: DataFetcher, sample_endpoint: NewsSource) -> None:
        """Test endpoint fetch with invalid JSON returns None when mirrors also fail."""
        respx.get(sample_endpoint.url).mock(return_value=Response(200, content=b"not json"))
        mock_mirrors_not_found()

        result = await fetcher.fetch()

        assert result is None

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_endpoint_with_params(self, logger: logging.Logger) -> None:
        """Test endpoint fetch includes query parameters."""
        source = NewsSource(url="https://api.example.com/news", params={"key": "value", "limit": "10"})
        fetcher = DataFetcher(source=source, logger=logger)

        mock_route = respx.get("https://api.example.com/news").mock(
            return_value=Response(200, json={"data": {"news": ["item"]}})
        )

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

    @pytest.fixture(autouse=True)
    def run_to_thread_inline(self) -> Iterator[Mock]:
        """Avoid sandbox thread hangs while preserving offload call semantics."""

        async def inline(func: Any, /, *args: Any, **kwargs: Any) -> Any:
            return func(*args, **kwargs)

        with patch("app.services.fetcher.asyncio.to_thread", side_effect=inline) as mock_to_thread:
            yield mock_to_thread

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
    @pytest.mark.parametrize(
        ("cache_date", "api_date", "use_api"),
        [
            ("2026-02-18", "2026-02-22", True),
            ("2026-02-22", "2026-02-20", False),
            ("unknown", "2026-02-22", True),
            ("2026-02-22", "unknown", False),
        ],
    )
    async def test_get_keeps_newer_news_when_api_is_not_today(
        self,
        cached_fetcher: CachedDataFetcher,
        sample_source: NewsSource,
        cache_date: str,
        api_date: str,
        use_api: bool,
    ) -> None:
        """API 未更新到今天时保留较新的新闻，缓存日期未知时采用 API。"""
        cached_data = self._make_news_data(cache_date)
        api_data = self._make_news_data(api_date)
        cached_fetcher.save_cache(cached_data)
        respx.get(sample_source.url).mock(
            return_value=Response(200, json=api_data["news"])
        )

        result = await cached_fetcher.get()

        expected = api_data if use_api else cached_data
        assert result == expected
        assert cached_fetcher.load_cache() == expected

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
        """Test get() returns stale cache when API and mirrors fail."""
        # Save cache with yesterday's date
        yesterday_data = self._make_news_data("2026-02-22")
        cached_fetcher.save_cache(yesterday_data)

        # Mock API returns 500 error and mirrors are unavailable
        respx.get(sample_source.url).mock(return_value=Response(500))
        mock_mirrors_not_found()

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

    @pytest.mark.asyncio
    async def test_get_concurrent_calls_fetch_once(
        self,
        cached_fetcher: CachedDataFetcher,
    ) -> None:
        """Concurrent misses share one fetch and the waiter reads the saved cache."""
        fetch_started = asyncio.Event()
        release_fetch = asyncio.Event()
        today_data = self._make_news_data("2026-02-23")

        async def fetch_once() -> dict[str, Any]:
            fetch_started.set()
            await release_fetch.wait()
            return today_data["news"]

        fetch_mock = AsyncMock(side_effect=fetch_once)
        cached_fetcher._fetcher.fetch = fetch_mock
        first = asyncio.create_task(cached_fetcher.get())
        await fetch_started.wait()
        second = asyncio.create_task(cached_fetcher.get())
        await asyncio.sleep(0)
        release_fetch.set()

        assert await asyncio.gather(first, second) == [today_data, today_data]
        fetch_mock.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_get_offloads_custom_cache_file_operations(
        self,
        cached_fetcher: CachedDataFetcher,
        run_to_thread_inline: Mock,
    ) -> None:
        """The news-date-aware get dispatches cache loads and saves to a thread."""
        today_data = self._make_news_data("2026-02-23")
        cached_fetcher.fetch_fresh = AsyncMock(return_value=today_data)

        await cached_fetcher.get()

        assert run_to_thread_inline.call_args_list == [
            ((cached_fetcher.load_cache,), {}),
            ((cached_fetcher.save_cache, today_data), {}),
        ]



class TestDataFetcherStaticFallback:
    """Tests for the 60s static-mirror fallback in DataFetcher."""

    @staticmethod
    def _mirror_payload(date_str: str) -> dict:
        """Build a raw 60s static mirror payload."""
        return {
            "date": date_str,
            "news": ["镜像新闻1", "镜像新闻2"],
            "updated": f"{date_str} 05:30",
            "updated_at": 1771000000000,
        }

    @pytest.fixture
    def source(self) -> NewsSource:
        return NewsSource(url="https://api.example.com/news", timeout_sec=5)

    @pytest.fixture
    def fetcher(self, source: NewsSource, logger: logging.Logger) -> DataFetcher:
        return DataFetcher(
            source=source,
            logger=logger,
            ghproxy_urls=["https://ghfast.top/"],
            date_provider=lambda: date(2026, 2, 23),
        )

    def test_build_static_urls_prefers_cdn_then_ghproxy(self, fetcher: DataFetcher) -> None:
        """Test static mirror URL ordering: CDN first, then ghproxy, then raw GitHub."""
        urls = fetcher._build_static_urls(date(2026, 2, 23))

        assert urls[0] == _NEWS_STATIC_CDN_URLS[0].format(date="2026-02-23")
        assert urls[1] == _NEWS_STATIC_CDN_URLS[1].format(date="2026-02-23")
        assert urls[2] == _NEWS_STATIC_CDN_URLS[2].format(date="2026-02-23")
        assert urls[3] == (
            "https://ghfast.top/"
            + _NEWS_STATIC_RAW_PATH.format(date="2026-02-23")
        )

    def test_build_static_urls_skips_prefix_without_scheme(
        self, source: NewsSource, logger: logging.Logger
    ) -> None:
        """Test invalid ghproxy prefixes (missing scheme) are ignored."""
        fetcher = DataFetcher(
            source=source,
            logger=logger,
            ghproxy_urls=["ghfast.top", "https://ghfast.top"],
        )

        urls = fetcher._build_static_urls(date(2026, 2, 23))

        assert not any(url.startswith("ghfast.top") for url in urls)
        assert (
            "https://ghfast.top/"
            + _NEWS_STATIC_RAW_PATH.format(date="2026-02-23")
            in urls
        )

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_uses_static_mirror_when_primary_fails(
        self, fetcher: DataFetcher, source: NewsSource
    ) -> None:
        """Test fetch() falls back to the static mirror and wraps the payload."""
        respx.get(source.url).mock(return_value=Response(403))
        respx.get(_NEWS_STATIC_CDN_URLS[0].format(date="2026-02-23")).mock(
            return_value=Response(200, json=self._mirror_payload("2026-02-23"))
        )

        result = await fetcher.fetch()

        assert result == {
            "code": 200,
            "message": "success",
            "data": self._mirror_payload("2026-02-23"),
        }

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_falls_back_to_yesterday_when_today_missing(
        self, fetcher: DataFetcher, source: NewsSource
    ) -> None:
        """Test fetch() tries yesterday's mirror file when today's is not published yet."""
        respx.get(source.url).mock(return_value=Response(403))
        for url_template in _NEWS_STATIC_CDN_URLS:
            respx.get(url_template.format(date="2026-02-23")).mock(
                return_value=Response(404)
            )
        respx.get(
            "https://ghfast.top/" + _NEWS_STATIC_RAW_PATH.format(date="2026-02-23")
        ).mock(return_value=Response(404))
        respx.get(_NEWS_STATIC_CDN_URLS[0].format(date="2026-02-22")).mock(
            return_value=Response(200, json=self._mirror_payload("2026-02-22"))
        )

        result = await fetcher.fetch()

        assert result is not None
        assert result["data"]["date"] == "2026-02-22"
        assert result["data"]["news"] == ["镜像新闻1", "镜像新闻2"]

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_returns_none_when_primary_and_mirrors_fail(
        self, fetcher: DataFetcher, source: NewsSource
    ) -> None:
        """Test fetch() returns None when the primary and every mirror fail."""
        respx.get(source.url).mock(return_value=Response(403))
        for url_template in _NEWS_STATIC_CDN_URLS:
            respx.get(url_template.format(date="2026-02-23")).mock(
                return_value=Response(404)
            )
            respx.get(url_template.format(date="2026-02-22")).mock(
                return_value=Response(404)
            )
        respx.get(
            "https://ghfast.top/"
            + _NEWS_STATIC_RAW_PATH.format(date="2026-02-23")
        ).mock(return_value=Response(404))
        respx.get(
            "https://ghfast.top/"
            + _NEWS_STATIC_RAW_PATH.format(date="2026-02-22")
        ).mock(return_value=Response(404))

        assert await fetcher.fetch() is None

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_skips_mirror_when_primary_succeeds(
        self, fetcher: DataFetcher, source: NewsSource
    ) -> None:
        """Test fetch() does not touch mirrors when the primary source works."""
        primary_payload = {"code": 200, "data": {"date": "2026-02-23", "news": ["x"]}}
        respx.get(source.url).mock(return_value=Response(200, json=primary_payload))

        result = await fetcher.fetch()

        assert result == primary_payload
        assert respx.calls.call_count == 1

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_ignores_mirror_payload_without_news(
        self, fetcher: DataFetcher, source: NewsSource
    ) -> None:
        """Test mirror responses lacking a non-empty news list are rejected."""
        respx.get(source.url).mock(return_value=Response(403))
        for url_template in _NEWS_STATIC_CDN_URLS:
            for date_str in ("2026-02-23", "2026-02-22"):
                respx.get(url_template.format(date=date_str)).mock(
                    return_value=Response(200, json={"date": date_str, "news": []})
                )
        for date_str in ("2026-02-23", "2026-02-22"):
            respx.get(
                "https://ghfast.top/"
                + _NEWS_STATIC_RAW_PATH.format(date=date_str)
            ).mock(return_value=Response(200, json={"date": date_str, "news": []}))

        assert await fetcher.fetch() is None

    @respx.mock
    @pytest.mark.asyncio
    async def test_cached_fetcher_updates_news_via_mirror(
        self, source: NewsSource, logger: logging.Logger, tmp_path: Path
    ) -> None:
        """Test CachedDataFetcher refreshes stale cache using the static mirror."""
        cached_fetcher = CachedDataFetcher(
            source=source,
            logger=logger,
            cache_dir=tmp_path,
            ghproxy_urls=["https://ghfast.top/"],
            date_provider=lambda: date(2026, 2, 23),
        )
        stale_data = {"news": {"code": 200, "data": {"date": "2026-02-22", "news": ["旧闻"]}}}
        cached_fetcher.save_cache(stale_data)

        respx.get(source.url).mock(return_value=Response(403))
        respx.get(_NEWS_STATIC_CDN_URLS[0].format(date="2026-02-23")).mock(
            return_value=Response(200, json=self._mirror_payload("2026-02-23"))
        )

        result = await cached_fetcher.get()

        assert result is not None
        assert result["news"]["data"]["date"] == "2026-02-23"
        assert result["news"]["data"]["news"] == ["镜像新闻1", "镜像新闻2"]
        assert cached_fetcher.load_cache() == result


class TestDataFetcherInstanceSwitching:
    """Tests for public 60s instance switching in DataFetcher."""

    INSTANCE_LIST_URL = "https://docs.example.com/instances.md"

    @staticmethod
    def _instance_markdown(*domains: str) -> str:
        """Build a docs-page-like markdown table for the given domains."""
        rows = "\n".join(
            f"| {domain} | ✅ | ✅ | tester | 2026-01-01 |" for domain in domains
        )
        return f"| 实例域名 | 大陆访问 | HTTPS | 提交人 | 添加时间 |\n| --- | --- | --- | --- | --- |\n{rows}\n"

    @pytest.fixture
    def source(self) -> NewsSource:
        return NewsSource(url="https://api.example.com/v2/60s", timeout_sec=5)

    @pytest.fixture
    def fetcher(self, source: NewsSource, logger: logging.Logger) -> DataFetcher:
        return DataFetcher(
            source=source,
            logger=logger,
            instances=InstancesConfig(
                urls=["https://inst1.example.com/", "https://inst2.example.com/prefix"]
            ),
        )

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_switches_to_available_instance(
        self, fetcher: DataFetcher, source: NewsSource
    ) -> None:
        """A blocked primary source falls through to the first healthy instance."""
        payload = {"code": 200, "data": {"date": "2026-02-23", "news": ["实例新闻"]}}
        respx.get(source.url).mock(return_value=Response(403))
        respx.get("https://inst1.example.com/v2/60s").mock(
            return_value=Response(200, json=payload)
        )
        inst2_route = respx.get("https://inst2.example.com/prefix/v2/60s")

        result = await fetcher.fetch()

        assert result == payload
        assert inst2_route.call_count == 0

    @respx.mock
    @pytest.mark.asyncio
    async def test_error_envelope_switches_to_available_instance(
        self, fetcher: DataFetcher, source: NewsSource
    ) -> None:
        """HTTP 成功但业务失败的主源信封仍触发公共实例回退。"""
        payload = {"code": 200, "data": {"news": ["实例新闻"]}}
        respx.get(source.url).mock(
            return_value=Response(200, json={"code": 500, "message": "err", "data": None})
        )
        instance_route = respx.get("https://inst1.example.com/v2/60s").mock(
            return_value=Response(200, json=payload)
        )
        inst2_route = respx.get("https://inst2.example.com/prefix/v2/60s")

        assert await fetcher.fetch() == payload
        assert instance_route.call_count == 1
        assert inst2_route.call_count == 0

    @respx.mock
    @pytest.mark.asyncio
    async def test_error_envelopes_fall_back_to_static_mirror(
        self, fetcher: DataFetcher, source: NewsSource
    ) -> None:
        """所有 API 端点返回错误信封时仍尝试并采用静态镜像。"""
        endpoints = (
            source.url,
            "https://inst1.example.com/v2/60s",
            "https://inst2.example.com/prefix/v2/60s",
        )
        routes = [
            respx.get(url).mock(
                return_value=Response(200, json={"code": 500, "data": None})
            )
            for url in endpoints
        ]
        payload = {"date": today_business().isoformat(), "news": ["镜像新闻"]}
        mirror_route = respx.get(
            _NEWS_STATIC_CDN_URLS[0].format(date=payload["date"])
        ).mock(return_value=Response(200, json=payload))

        assert await fetcher.fetch() == {
            "code": 200,
            "message": "success",
            "data": payload,
        }
        assert all(route.call_count == 1 for route in routes)
        assert mirror_route.call_count == 1

    @respx.mock
    @pytest.mark.asyncio
    async def test_preferred_instance_is_tried_first_next_time(
        self, fetcher: DataFetcher, source: NewsSource
    ) -> None:
        """The last successful instance is preferred and the blocked primary is skipped."""
        payload = {"code": 200, "data": {"date": "2026-02-23", "news": ["实例新闻"]}}
        primary_route = respx.get(source.url).mock(return_value=Response(403))
        respx.get("https://inst1.example.com/v2/60s").mock(
            return_value=Response(200, json=payload)
        )

        await fetcher.fetch()
        await fetcher.fetch()

        assert primary_route.call_count == 1
        assert respx.calls.call_count == 3

    @respx.mock
    @pytest.mark.asyncio
    async def test_discovered_instances_are_used(
        self, logger: logging.Logger
    ) -> None:
        """Instances discovered from the docs page are used as fallbacks."""
        source = NewsSource(url="https://api.example.com/v2/60s", timeout_sec=5)
        fetcher = DataFetcher(
            source=source,
            logger=logger,
            instances=InstancesConfig(list_url=self.INSTANCE_LIST_URL),
        )
        payload = {"code": 200, "data": {"date": "2026-02-23", "news": ["发现新闻"]}}
        respx.get(self.INSTANCE_LIST_URL).mock(
            return_value=Response(200, text=self._instance_markdown("60s.inst.example.com"))
        )
        respx.get(source.url).mock(return_value=Response(403))
        respx.get("https://60s.inst.example.com/v2/60s").mock(
            return_value=Response(200, json=payload)
        )

        result = await fetcher.fetch()

        assert result == payload

    @respx.mock
    @pytest.mark.asyncio
    async def test_falls_back_to_mirror_when_all_instances_fail(
        self, fetcher: DataFetcher, source: NewsSource
    ) -> None:
        """Static mirror is used when every API endpoint fails."""
        for url in (
            source.url,
            "https://inst1.example.com/v2/60s",
            "https://inst2.example.com/prefix/v2/60s",
        ):
            respx.get(url).mock(return_value=Response(403))
        respx.get(_NEWS_STATIC_CDN_URLS[0].format(date=today_business().isoformat())).mock(
            return_value=Response(
                200,
                json={"date": today_business().isoformat(), "news": ["镜像新闻"]},
            )
        )

        result = await fetcher.fetch()

        assert result is not None
        assert result["data"]["news"] == ["镜像新闻"]
