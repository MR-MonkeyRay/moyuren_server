"""数据获取与实例健康指标测试。"""

import asyncio
import logging
from collections.abc import Callable
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.config import NewsSource, StockIndexSource
from app.core.metrics import (
    instance_consecutive_failures,
    instance_cooling_down,
    registry,
)
from app.services.daily_cache import DailyCache
from app.services.fetcher import CachedDataFetcher
from app.services.instances import InstanceRouter
from app.services.stock_index import StockIndexService


class _MetricCache(DailyCache[dict[str, str]]):
    """用于验证 DailyCache 指标分支的最小实现。"""

    def __init__(
        self,
        namespace: str,
        cache_dir: Path,
        logger: logging.Logger,
        fresh_data: dict[str, str] | None,
    ) -> None:
        super().__init__(namespace, cache_dir, logger)
        self.fresh_data = fresh_data

    async def fetch_fresh(self) -> dict[str, str] | None:
        return self.fresh_data


def _sample(name: str, labels: dict[str, str]) -> float:
    """读取当前样本值，未创建的标签组合按零处理。"""
    return registry.get_sample_value(name, labels) or 0.0


def _source_samples(source: str) -> dict[str, float]:
    """读取来源的全部结果计数，用于验证一次调用只有一个结果。"""
    return {
        result: _sample(
            "moyuren_source_fetch_total", {"source": source, "result": result}
        )
        for result in ("cache", "fresh", "stale", "failed")
    }


def _assert_single_outcome(
    before: dict[str, float], after: dict[str, float], expected: str
) -> None:
    """断言仅预期结果增加一次，其他结果不变。"""
    assert {
        result: after[result] - before[result] for result in before
    } == {
        "cache": float(expected == "cache"),
        "fresh": float(expected == "fresh"),
        "stale": float(expected == "stale"),
        "failed": float(expected == "failed"),
    }


@pytest.fixture(autouse=True)
def run_to_thread_inline(monkeypatch: pytest.MonkeyPatch) -> None:
    """避免沙箱线程调度挂起，同时保留异步调用边界。"""

    async def inline(
        func: Callable[..., Any], /, *args: Any, **kwargs: Any
    ) -> Any:
        return func(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", inline)


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["cache", "fresh", "stale", "failed"])
async def test_daily_cache_records_each_outcome_once(
    outcome: str, tmp_path: Path, logger: logging.Logger
) -> None:
    """DailyCache 每次 get 仅增加对应结果计数一次。"""
    namespace = f"daily_metric_{outcome}"
    cache = _MetricCache(
        namespace,
        tmp_path,
        logger,
        {"value": "fresh"} if outcome == "fresh" else None,
    )
    if outcome in {"cache", "stale"}:
        cache.save_cache({"value": "cached"})
    if outcome == "stale":
        cache.is_cache_valid = lambda: False

    before = _source_samples(namespace)
    result = await cache.get()

    _assert_single_outcome(before, _source_samples(namespace), outcome)
    assert cache.last_outcome == outcome
    assert result is None if outcome == "failed" else result is not None


@pytest.mark.asyncio
async def test_daily_cache_force_refresh_skips_valid_cache(
    tmp_path: Path, logger: logging.Logger
) -> None:
    """强制刷新忽略有效缓存，并只记录 fresh。"""
    namespace = "daily_metric_force"
    cache = _MetricCache(namespace, tmp_path, logger, {"value": "fresh"})
    cache.save_cache({"value": "cached"})
    before = _source_samples(namespace)

    result = await cache.get(force_refresh=True)

    assert result == {"value": "fresh"}
    _assert_single_outcome(before, _source_samples(namespace), "fresh")


@pytest.mark.asyncio
async def test_daily_cache_exception_uses_stale_cache(
    tmp_path: Path, logger: logging.Logger
) -> None:
    """网络异常降级到旧缓存，并只记录 stale。"""
    namespace = "daily_metric_exception"
    cache = _MetricCache(namespace, tmp_path, logger, None)
    cache.save_cache({"value": "cached"})
    cache.is_cache_valid = lambda: False
    cache.fetch_fresh = AsyncMock(side_effect=RuntimeError("unavailable"))
    before = _source_samples(namespace)

    result = await cache.get()

    assert result == {"value": "cached"}
    _assert_single_outcome(before, _source_samples(namespace), "stale")


def _news_data(news_date: str) -> dict[str, Any]:
    """构造带日期的新闻缓存信封。"""
    return {
        "news": {
            "code": 200,
            "data": {"date": news_date, "news": ["item"]},
        }
    }


def _cached_fetcher(
    tmp_path: Path, logger: logging.Logger
) -> CachedDataFetcher:
    """创建固定业务日期的新闻缓存服务。"""
    return CachedDataFetcher(
        NewsSource(url="https://api.example.com/news"),
        logger,
        tmp_path,
        date_provider=lambda: date(2026, 2, 23),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case", "expected"),
    [
        ("cache", "cache"),
        ("fresh_today", "fresh"),
        ("fresh_yesterday", "fresh"),
        ("unknown_no_cache", "fresh"),
        ("unknown_keep_cache", "stale"),
        ("older_keep_cache", "stale"),
        ("newer_old_api", "fresh"),
        ("fetch_failed", "stale"),
        ("nothing", "failed"),
    ],
)
async def test_cached_data_fetcher_records_date_aware_paths(
    case: str,
    expected: str,
    tmp_path: Path,
    logger: logging.Logger,
) -> None:
    """新闻日期感知分支按实际返回来源记录结果。"""
    fetcher = _cached_fetcher(tmp_path, logger)
    if case == "cache":
        fetcher.save_cache(_news_data("2026-02-23"))
    elif case in {"unknown_keep_cache", "older_keep_cache", "fetch_failed"}:
        fetcher.save_cache(_news_data("2026-02-22"))
    elif case == "newer_old_api":
        fetcher.save_cache(_news_data("2026-02-20"))

    fresh_by_case: dict[str, dict[str, Any] | None] = {
        "cache": None,
        "fresh_today": _news_data("2026-02-23"),
        "fresh_yesterday": _news_data("2026-02-22"),
        "unknown_no_cache": _news_data("unknown"),
        "unknown_keep_cache": _news_data("unknown"),
        "older_keep_cache": _news_data("2026-02-21"),
        "newer_old_api": _news_data("2026-02-22"),
        "fetch_failed": None,
        "nothing": None,
    }
    fetcher.fetch_fresh = AsyncMock(return_value=fresh_by_case[case])
    before = _source_samples("news")

    result = await fetcher.get()

    _assert_single_outcome(before, _source_samples("news"), expected)
    assert fetcher.last_outcome == expected
    assert result is None if case == "nothing" else result is not None
    if case == "cache":
        fetcher.fetch_fresh.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("fresh_data", "has_cache", "expected"),
    [
        (_news_data("2026-02-22"), True, "fresh"),
        (None, True, "stale"),
        (None, False, "failed"),
    ],
)
async def test_cached_data_fetcher_records_force_refresh_paths(
    fresh_data: dict[str, Any] | None,
    has_cache: bool,
    expected: str,
    tmp_path: Path,
    logger: logging.Logger,
) -> None:
    """强制刷新成功、缓存降级与完全失败各记录一次。"""
    fetcher = _cached_fetcher(tmp_path, logger)
    if has_cache:
        fetcher.save_cache(_news_data("2026-02-21"))
    fetcher.fetch_fresh = AsyncMock(return_value=fresh_data)
    before = _source_samples("news")

    result = await fetcher.get(force_refresh=True)

    _assert_single_outcome(before, _source_samples("news"), expected)
    assert fetcher.last_outcome == expected
    assert result is None if expected == "failed" else result is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["cache", "fresh", "stale", "failed"])
async def test_stock_index_records_each_outcome(outcome: str) -> None:
    """股票服务的独立缓存与降级分支使用统一来源指标。"""
    config = StockIndexSource(
        quote_url="https://stock.example.com/quote",
        secids=["1.000001"],
        cache_ttl_sec=60,
        market_timezones={"A": "Asia/Shanghai"},
    )
    with patch("app.services.stock_index.xcals.get_calendar") as get_calendar:
        calendar = MagicMock()
        calendar.is_session.return_value = True
        get_calendar.return_value = calendar
        service = StockIndexService(config)
    now = datetime(2026, 2, 23, 10, tzinfo=timezone.utc)
    cached = {"items": [], "is_stale": False}
    if outcome == "cache":
        service._cache = {"data": cached, "fetched_at": now.timestamp()}
    elif outcome == "stale":
        service._cache = {"data": cached, "fetched_at": 0}
        service._fetch_quotes = AsyncMock(side_effect=RuntimeError("unavailable"))
    elif outcome == "failed":
        service._fetch_quotes = AsyncMock(side_effect=RuntimeError("unavailable"))
    else:
        service._fetch_quotes = AsyncMock(return_value=[])
    before = _source_samples("stock_index")

    result = await service.fetch_indices(now)

    _assert_single_outcome(before, _source_samples("stock_index"), outcome)
    assert service.last_outcome == outcome
    if outcome == "failed":
        assert result["is_data_missing"] is True
    if outcome == "stale":
        assert result["is_stale"] is True


def test_instance_metrics_use_credential_free_bounded_endpoint(
    logger: logging.Logger,
) -> None:
    """实例指标仅使用 scheme、host 与 port 作为标签。"""
    router = InstanceRouter(logger, failure_threshold=2, cooldown_sec=60)
    url = "https://user:secret@example.com:8443/private/path?token=hidden"
    labels = {"endpoint": "https://example.com:8443"}

    router.mark_failure(url)
    assert _sample("moyuren_instance_consecutive_failures", labels) == 1.0
    assert _sample("moyuren_instance_cooling_down", labels) == 0.0

    router.mark_failure(url)
    assert _sample("moyuren_instance_consecutive_failures", labels) == 2.0
    assert _sample("moyuren_instance_cooling_down", labels) == 1.0

    router.mark_success(url)
    assert _sample("moyuren_instance_consecutive_failures", labels) == 0.0
    assert _sample("moyuren_instance_cooling_down", labels) == 0.0
    endpoint_labels = {
        sample.labels["endpoint"]
        for metric in (instance_consecutive_failures, instance_cooling_down)
        for sample in metric.collect()[0].samples
        if "endpoint" in sample.labels and "example.com" in sample.labels["endpoint"]
    }
    assert endpoint_labels == {"https://example.com:8443"}
