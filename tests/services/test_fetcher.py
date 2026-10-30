"""app/services/fetcher.py 数据获取服务测试。"""

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
    """将今日和昨日的所有静态新闻镜像 URL 模拟为返回 HTTP 404。"""
    today = today_business()
    for target in (today, today - timedelta(days=1)):
        for template in _NEWS_STATIC_CDN_URLS:
            respx.get(template.format(date=target.isoformat())).mock(
                return_value=Response(404)
            )


class TestDataFetcher:
    """DataFetcher 类测试。"""

    @pytest.fixture
    def sample_endpoint(self) -> NewsSource:
        """创建新闻源配置样例。"""
        return NewsSource(url="https://api.example.com/news", timeout_sec=10, params={"force-update": "false"})

    @pytest.fixture
    def fetcher(self, sample_endpoint: NewsSource, logger: logging.Logger) -> DataFetcher:
        """创建 DataFetcher 实例。"""
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
        """测试从端点成功获取数据。"""
        mock_data = {"code": 200, "data": {"news": ["Item 1", "Item 2"]}}
        respx.get(sample_endpoint.url).mock(return_value=Response(200, json=mock_data))

        result = await fetcher.fetch()

        assert result == mock_data
        assert respx.calls.call_count == 1

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_endpoint_timeout(self, fetcher: DataFetcher, sample_endpoint: NewsSource) -> None:
        """测试端点请求超时且镜像也失败时返回 None。"""
        respx.get(sample_endpoint.url).mock(side_effect=httpx.TimeoutException("Timeout"))
        mock_mirrors_not_found()

        result = await fetcher.fetch()

        assert result is None

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_endpoint_http_error(self, fetcher: DataFetcher, sample_endpoint: NewsSource) -> None:
        """测试端点返回 HTTP 错误且镜像也失败时返回 None。"""
        respx.get(sample_endpoint.url).mock(return_value=Response(500))
        mock_mirrors_not_found()

        result = await fetcher.fetch()

        assert result is None

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_endpoint_request_error(self, fetcher: DataFetcher, sample_endpoint: NewsSource) -> None:
        """测试端点请求出错且镜像也失败时返回 None。"""
        respx.get(sample_endpoint.url).mock(side_effect=httpx.ConnectError("Connection failed"))
        mock_mirrors_not_found()

        result = await fetcher.fetch()

        assert result is None

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_endpoint_invalid_json(self, fetcher: DataFetcher, sample_endpoint: NewsSource) -> None:
        """测试端点返回无效 JSON 且镜像也失败时返回 None。"""
        respx.get(sample_endpoint.url).mock(return_value=Response(200, content=b"not json"))
        mock_mirrors_not_found()

        result = await fetcher.fetch()

        assert result is None

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_endpoint_with_params(self, logger: logging.Logger) -> None:
        """测试端点请求包含查询参数。"""
        source = NewsSource(url="https://api.example.com/news", params={"key": "value", "limit": "10"})
        fetcher = DataFetcher(source=source, logger=logger)

        mock_route = respx.get("https://api.example.com/news").mock(
            return_value=Response(200, json={"data": {"news": ["item"]}})
        )

        await fetcher.fetch()

        assert mock_route.called
        # 验证请求包含查询参数
        request = respx.calls.last.request
        assert "key=value" in str(request.url)
        assert "limit=10" in str(request.url)


class TestCachedDataFetcher:
    """CachedDataFetcher 类测试。"""

    @staticmethod
    def _make_news_data(date_str: str) -> dict:
        """构造标准 API 响应数据的辅助函数。"""
        return {"news": {"code": 200, "data": {"date": date_str, "news": ["item1"]}}}

    @pytest.fixture
    def sample_source(self) -> NewsSource:
        """创建新闻源配置样例。"""
        return NewsSource(url="https://api.example.com/news")

    @pytest.fixture
    def cached_fetcher(
        self, sample_source: NewsSource, logger: logging.Logger, tmp_path: Path
    ) -> CachedDataFetcher:
        """创建使用固定日期的 CachedDataFetcher 实例。"""
        return CachedDataFetcher(
            source=sample_source,
            logger=logger,
            cache_dir=tmp_path,
            date_provider=lambda: date(2026, 2, 23),
        )

    @pytest.fixture(autouse=True)
    def run_to_thread_inline(self) -> Iterator[Mock]:
        """避免沙箱线程挂起，同时保留任务转交线程执行的调用语义。"""

        async def inline(func: Any, /, *args: Any, **kwargs: Any) -> Any:
            return func(*args, **kwargs)

        with patch("app.services.fetcher.asyncio.to_thread", side_effect=inline) as mock_to_thread:
            yield mock_to_thread

    # _extract_news_date 测试

    def test_extract_news_date_iso_format(
        self, cached_fetcher: CachedDataFetcher
    ) -> None:
        """测试 _extract_news_date 解析 ISO 日期格式（YYYY-MM-DD）。"""
        data = self._make_news_data("2026-02-23")
        result = cached_fetcher._extract_news_date(data)
        assert result == date(2026, 2, 23)

    def test_extract_news_date_slash_format(
        self, cached_fetcher: CachedDataFetcher
    ) -> None:
        """测试 _extract_news_date 解析斜线日期格式（YYYY/MM/DD）。"""
        data = self._make_news_data("2026/02/23")
        result = cached_fetcher._extract_news_date(data)
        assert result == date(2026, 2, 23)

    def test_extract_news_date_chinese_format(
        self, cached_fetcher: CachedDataFetcher
    ) -> None:
        """测试 _extract_news_date 解析中文日期格式（YYYY年M月D日）。"""
        data = self._make_news_data("2026年2月4日")
        result = cached_fetcher._extract_news_date(data)
        assert result == date(2026, 2, 4)

    def test_extract_news_date_none_data(
        self, cached_fetcher: CachedDataFetcher
    ) -> None:
        """测试 _extract_news_date 在输入为 None 时返回 None。"""
        result = cached_fetcher._extract_news_date(None)
        assert result is None

    def test_extract_news_date_invalid_format(
        self, cached_fetcher: CachedDataFetcher
    ) -> None:
        """测试 _extract_news_date 在格式无效时返回 None。"""
        data = {"news": {"data": {"date": "invalid"}}}
        result = cached_fetcher._extract_news_date(data)
        assert result is None

    # get() 方法测试

    @respx.mock
    @pytest.mark.asyncio
    async def test_get_cache_hit_today(
        self, cached_fetcher: CachedDataFetcher, sample_source: NewsSource
    ) -> None:
        """测试缓存新闻日期为今日时，get() 返回缓存。"""
        # 保存日期为今日的缓存
        today_data = self._make_news_data("2026-02-23")
        cached_fetcher.save_cache(today_data)

        # 模拟 API（不应被调用）
        respx.get(sample_source.url).mock(return_value=Response(200, json={"should": "not be called"}))

        result = await cached_fetcher.get()

        assert result == today_data
        assert respx.calls.call_count == 0  # 不发起 HTTP 请求

    @respx.mock
    @pytest.mark.asyncio
    async def test_get_api_date_matches_today(
        self, cached_fetcher: CachedDataFetcher, sample_source: NewsSource
    ) -> None:
        """测试 API 日期为今日时，get() 保存并返回 API 数据。"""
        # 无缓存
        today_data = self._make_news_data("2026-02-23")
        respx.get(sample_source.url).mock(return_value=Response(200, json=today_data["news"]))

        result = await cached_fetcher.get()

        assert result == today_data
        # 验证缓存已保存
        cached_data = cached_fetcher.load_cache()
        assert cached_data == today_data

    @respx.mock
    @pytest.mark.asyncio
    async def test_get_api_not_updated_keep_cache(
        self, cached_fetcher: CachedDataFetcher, sample_source: NewsSource
    ) -> None:
        """测试 API 返回过期日期时，get() 保留本地缓存。"""
        # 保存日期为昨日的缓存
        yesterday_data = self._make_news_data("2026-02-22")
        cached_fetcher.save_cache(yesterday_data)

        # 模拟 API 返回昨日日期（尚未更新）
        api_response = {"code": 200, "data": {"date": "2026-02-22", "news": ["new item"]}}
        respx.get(sample_source.url).mock(return_value=Response(200, json=api_response))

        result = await cached_fetcher.get()

        # 应返回本地缓存，而非 API 数据
        assert result == yesterday_data
        assert result["news"]["data"]["news"] == ["item1"]  # 原有缓存

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
        """测试无缓存且 API 返回过期日期时，get() 保存 API 数据。"""
        # 无缓存
        yesterday_data = self._make_news_data("2026-02-22")
        respx.get(sample_source.url).mock(return_value=Response(200, json=yesterday_data["news"]))

        result = await cached_fetcher.get()

        # 应保存并返回 API 数据（总比没有好）
        assert result == yesterday_data
        cached_data = cached_fetcher.load_cache()
        assert cached_data == yesterday_data

    @respx.mock
    @pytest.mark.asyncio
    async def test_get_api_failure_fallback(
        self, cached_fetcher: CachedDataFetcher, sample_source: NewsSource
    ) -> None:
        """测试 API 和镜像均失败时，get() 返回过期缓存。"""
        # 保存日期为昨日的缓存
        yesterday_data = self._make_news_data("2026-02-22")
        cached_fetcher.save_cache(yesterday_data)

        # 模拟 API 返回 500 错误且镜像不可用
        respx.get(sample_source.url).mock(return_value=Response(500))
        mock_mirrors_not_found()

        result = await cached_fetcher.get()

        # 应回退到过期缓存
        assert result == yesterday_data

    @respx.mock
    @pytest.mark.asyncio
    async def test_get_force_refresh(
        self, cached_fetcher: CachedDataFetcher, sample_source: NewsSource
    ) -> None:
        """测试 get(force_refresh=True) 调用 API 并保存新数据。"""
        # 保存日期为昨日的缓存
        yesterday_data = self._make_news_data("2026-02-22")
        cached_fetcher.save_cache(yesterday_data)

        # 模拟 API 返回今日日期
        today_data = self._make_news_data("2026-02-23")
        respx.get(sample_source.url).mock(return_value=Response(200, json=today_data["news"]))

        result = await cached_fetcher.get(force_refresh=True)

        # 应调用 API 并返回新数据
        assert result == today_data
        assert respx.calls.call_count == 1
        # 验证缓存已更新
        cached_data = cached_fetcher.load_cache()
        assert cached_data == today_data

    @pytest.mark.asyncio
    async def test_get_concurrent_calls_fetch_once(
        self,
        cached_fetcher: CachedDataFetcher,
    ) -> None:
        """并发缓存未命中时共用一次获取，等待方读取已保存的缓存。"""
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
        """按新闻日期判断的 get 将缓存读写交给线程执行。"""
        today_data = self._make_news_data("2026-02-23")
        cached_fetcher.fetch_fresh = AsyncMock(return_value=today_data)

        await cached_fetcher.get()

        assert run_to_thread_inline.call_args_list == [
            ((cached_fetcher.load_cache,), {}),
            ((cached_fetcher.save_cache, today_data), {}),
        ]



class TestDataFetcherStaticFallback:
    """DataFetcher 的 60s 静态镜像回退测试。"""

    @staticmethod
    def _mirror_payload(date_str: str) -> dict:
        """构造 60s 静态镜像的原始响应数据。"""
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
        """测试静态镜像 URL 顺序：CDN、ghproxy、GitHub 原始地址。"""
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
        """测试忽略缺少协议的无效 ghproxy 前缀。"""
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
        """测试 fetch() 回退到静态镜像并封装响应数据。"""
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
        """测试今日镜像文件尚未发布时，fetch() 尝试昨日文件。"""
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
        """测试主源和所有镜像均失败时，fetch() 返回 None。"""
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
        """测试主源可用时，fetch() 不访问镜像。"""
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
        """测试拒绝不含非空新闻列表的镜像响应。"""
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
        """测试 CachedDataFetcher 使用静态镜像刷新过期缓存。"""
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
    """DataFetcher 的 60s 公共实例切换测试。"""

    INSTANCE_LIST_URL = "https://docs.example.com/instances.md"

    @staticmethod
    def _instance_markdown(*domains: str) -> str:
        """为给定域名构造与文档页面类似的 Markdown 表格。"""
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
        """主源被阻断时回退到首个可用实例。"""
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
        """优先使用上次成功的实例，并跳过被阻断的主源。"""
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
        """将文档页面中发现的实例用于回退。"""
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
        """所有 API 端点均失败时使用静态镜像。"""
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
