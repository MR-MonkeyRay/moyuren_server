"""app/services/daily_cache.py 日级缓存服务测试。"""

import asyncio
import json
import logging
from datetime import date
from pathlib import Path
from typing import Any, Iterator
from unittest.mock import AsyncMock, Mock, patch

import pytest

from app.services.daily_cache import DailyCache


class ConcreteDailyCache(DailyCache[dict[str, Any]]):
    """用于测试的具体实现。"""

    def __init__(self, namespace: str, cache_dir: Path, logger: logging.Logger):
        super().__init__(namespace, cache_dir, logger)
        self.fetch_fresh_mock = AsyncMock(return_value={"key": "value"})

    async def fetch_fresh(self) -> dict[str, Any] | None:
        return await self.fetch_fresh_mock()


class TestDailyCache:
    """DailyCache 类测试。"""

    @pytest.fixture
    def cache_dir(self, tmp_path: Path) -> Path:
        """创建临时缓存目录。"""
        cache_dir = tmp_path / "daily"
        cache_dir.mkdir()
        return cache_dir

    @pytest.fixture
    def cache(self, cache_dir: Path, logger: logging.Logger) -> ConcreteDailyCache:
        """创建 ConcreteDailyCache 实例。"""
        return ConcreteDailyCache("test", cache_dir, logger)

    @pytest.fixture(autouse=True)
    def run_to_thread_inline(self) -> Iterator[Mock]:
        """避免沙箱线程挂起，同时保留任务转交线程执行的调用语义。"""

        async def inline(func: Any, /, *args: Any, **kwargs: Any) -> Any:
            return func(*args, **kwargs)

        with patch("app.services.daily_cache.asyncio.to_thread", side_effect=inline) as mock_to_thread:
            yield mock_to_thread

    def test_cache_file_path(self, cache: ConcreteDailyCache, cache_dir: Path) -> None:
        """验证缓存文件路径正确。"""
        expected_path = cache_dir / "test.json"
        assert cache._get_cache_file() == expected_path

    def test_is_cache_valid_no_file(self, cache: ConcreteDailyCache) -> None:
        """验证文件不存在时缓存无效。"""
        assert cache.is_cache_valid() is False

    @patch("app.services.daily_cache.today_business")
    def test_is_cache_valid_same_day(self, mock_today: AsyncMock, cache: ConcreteDailyCache, cache_dir: Path) -> None:
        """验证日期为今天时缓存有效。"""
        mock_today.return_value = date(2026, 2, 5)

        # 创建缓存文件
        cache_file = cache_dir / "test.json"
        cache_file.write_text(json.dumps({"date": "2026-02-05", "data": {"key": "value"}, "fetched_at": 1738713600000}))

        assert cache.is_cache_valid() is True

    @patch("app.services.daily_cache.today_business")
    def test_is_cache_valid_different_day(
        self, mock_today: AsyncMock, cache: ConcreteDailyCache, cache_dir: Path
    ) -> None:
        """验证日期不是今天时缓存无效。"""
        mock_today.return_value = date(2026, 2, 6)

        # 创建缓存文件（昨天的日期）
        cache_file = cache_dir / "test.json"
        cache_file.write_text(json.dumps({"date": "2026-02-05", "data": {"key": "value"}, "fetched_at": 1738713600000}))

        assert cache.is_cache_valid() is False

    def test_load_cache_success(self, cache: ConcreteDailyCache, cache_dir: Path) -> None:
        """验证成功加载缓存数据。"""
        # 创建缓存文件
        cache_file = cache_dir / "test.json"
        expected_data = {"key": "value", "nested": {"foo": "bar"}}
        cache_file.write_text(json.dumps({"date": "2026-02-05", "data": expected_data, "fetched_at": 1738713600000}))

        result = cache.load_cache()
        assert result == expected_data

    def test_load_cache_no_file(self, cache: ConcreteDailyCache) -> None:
        """验证文件不存在时加载缓存返回 None。"""
        result = cache.load_cache()
        assert result is None

    def test_load_cache_invalid_json(self, cache: ConcreteDailyCache, cache_dir: Path) -> None:
        """验证 JSON 无效时加载缓存返回 None。"""
        cache_file = cache_dir / "test.json"
        cache_file.write_text("invalid json{")

        result = cache.load_cache()
        assert result is None

    @patch("app.services.daily_cache.today_business")
    def test_save_cache(self, mock_today: AsyncMock, cache: ConcreteDailyCache, cache_dir: Path) -> None:
        """验证以原子写入方式保存缓存数据。"""
        mock_today.return_value = date(2026, 2, 5)

        data = {"key": "value", "nested": {"foo": "bar"}}
        cache.save_cache(data)

        # 验证缓存文件存在
        cache_file = cache_dir / "test.json"
        assert cache_file.exists()

        # 验证缓存内容
        with cache_file.open("r", encoding="utf-8") as f:
            cache_data = json.load(f)

        assert cache_data["date"] == "2026-02-05"
        assert cache_data["data"] == data
        assert "fetched_at" in cache_data
        assert isinstance(cache_data["fetched_at"], int)

    @patch("app.services.daily_cache.today_business")
    @pytest.mark.asyncio
    async def test_get_uses_valid_cache(
        self, mock_today: AsyncMock, cache: ConcreteDailyCache, cache_dir: Path
    ) -> None:
        """验证 get() 使用有效缓存，不获取新数据。"""
        mock_today.return_value = date(2026, 2, 5)

        # 创建有效缓存
        cache_file = cache_dir / "test.json"
        cached_data = {"cached": "data"}
        cache_file.write_text(json.dumps({"date": "2026-02-05", "data": cached_data, "fetched_at": 1738713600000}))

        result = await cache.get()

        # 应该返回缓存数据
        assert result == cached_data
        # 不应该调用 fetch_fresh
        cache.fetch_fresh_mock.assert_not_called()

    @patch("app.services.daily_cache.today_business")
    @pytest.mark.asyncio
    async def test_get_fetches_fresh_when_expired(
        self, mock_today: AsyncMock, cache: ConcreteDailyCache, cache_dir: Path
    ) -> None:
        """验证缓存过期时 get() 获取新数据。"""
        mock_today.return_value = date(2026, 2, 6)

        # 创建过期缓存（昨天的日期）
        cache_file = cache_dir / "test.json"
        cache_file.write_text(json.dumps({"date": "2026-02-05", "data": {"old": "data"}, "fetched_at": 1738713600000}))

        # 设置 fetch_fresh 返回新数据
        fresh_data = {"fresh": "data"}
        cache.fetch_fresh_mock.return_value = fresh_data

        result = await cache.get()

        # 应该返回新数据
        assert result == fresh_data
        # 应该调用 fetch_fresh
        cache.fetch_fresh_mock.assert_called_once()

        # 验证新数据已保存到缓存
        with cache_file.open("r", encoding="utf-8") as f:
            cache_data = json.load(f)
        assert cache_data["data"] == fresh_data
        assert cache_data["date"] == "2026-02-06"

    @patch("app.services.daily_cache.today_business")
    @pytest.mark.asyncio
    async def test_get_fallback_to_stale_cache(
        self, mock_today: AsyncMock, cache: ConcreteDailyCache, cache_dir: Path
    ) -> None:
        """验证获取新数据失败时 get() 降级使用过期缓存。"""
        mock_today.return_value = date(2026, 2, 6)

        # 创建过期缓存
        cache_file = cache_dir / "test.json"
        stale_data = {"stale": "data"}
        cache_file.write_text(json.dumps({"date": "2026-02-05", "data": stale_data, "fetched_at": 1738713600000}))

        # 设置 fetch_fresh 返回 None（模拟网络失败）
        cache.fetch_fresh_mock.return_value = None

        result = await cache.get()

        # 应该返回过期缓存（降级策略）
        assert result == stale_data
        # 应该调用 fetch_fresh
        cache.fetch_fresh_mock.assert_called_once()

    @patch("app.services.daily_cache.today_business")
    @pytest.mark.asyncio
    async def test_get_returns_none_when_no_cache_and_fetch_fails(
        self, mock_today: AsyncMock, cache: ConcreteDailyCache
    ) -> None:
        """验证无缓存且获取失败时 get() 返回 None。"""
        mock_today.return_value = date(2026, 2, 5)

        # 设置 fetch_fresh 返回 None
        cache.fetch_fresh_mock.return_value = None

        result = await cache.get()

        # 应该返回 None
        assert result is None
        # 应该调用 fetch_fresh
        cache.fetch_fresh_mock.assert_called_once()

    @patch("app.services.daily_cache.today_business")
    @pytest.mark.asyncio
    async def test_get_force_refresh(self, mock_today: AsyncMock, cache: ConcreteDailyCache, cache_dir: Path) -> None:
        """验证 force_refresh=True 时 get() 忽略有效缓存。"""
        mock_today.return_value = date(2026, 2, 5)

        # 创建有效缓存
        cache_file = cache_dir / "test.json"
        cache_file.write_text(
            json.dumps({"date": "2026-02-05", "data": {"cached": "data"}, "fetched_at": 1738713600000})
        )

        # 设置 fetch_fresh 返回新数据
        fresh_data = {"fresh": "data"}
        cache.fetch_fresh_mock.return_value = fresh_data

        result = await cache.get(force_refresh=True)

        # 应该返回新数据
        assert result == fresh_data
        # 应该调用 fetch_fresh（即使缓存有效）
        cache.fetch_fresh_mock.assert_called_once()

        # 验证新数据已保存到缓存
        with cache_file.open("r", encoding="utf-8") as f:
            cache_data = json.load(f)
        assert cache_data["data"] == fresh_data

    @pytest.mark.asyncio
    async def test_get_concurrent_calls_fetch_once(self, cache: ConcreteDailyCache) -> None:
        """并发缓存未命中时共用一次获取，随后使用已保存的值。"""
        fetch_started = asyncio.Event()
        release_fetch = asyncio.Event()
        fresh_data = {"fresh": "data"}

        async def fetch_once() -> dict[str, str]:
            fetch_started.set()
            await release_fetch.wait()
            return fresh_data

        cache.fetch_fresh_mock.side_effect = fetch_once
        first = asyncio.create_task(cache.get())
        await fetch_started.wait()
        second = asyncio.create_task(cache.get())
        await asyncio.sleep(0)
        release_fetch.set()

        assert await asyncio.gather(first, second) == [fresh_data, fresh_data]
        cache.fetch_fresh_mock.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_get_force_refresh_refetches_serially(self, cache: ConcreteDailyCache) -> None:
        """强制刷新保持串行，每次均跳过缓存检查。"""
        active_fetches = 0
        max_active_fetches = 0

        async def fetch_fresh() -> dict[str, int]:
            nonlocal active_fetches, max_active_fetches
            active_fetches += 1
            max_active_fetches = max(max_active_fetches, active_fetches)
            await asyncio.sleep(0)
            active_fetches -= 1
            return {"call": cache.fetch_fresh_mock.await_count}

        cache.fetch_fresh_mock.side_effect = fetch_fresh

        results = await asyncio.gather(cache.get(force_refresh=True), cache.get(force_refresh=True))

        assert results == [{"call": 1}, {"call": 2}]
        assert cache.fetch_fresh_mock.await_count == 2
        assert max_active_fetches == 1

    @pytest.mark.asyncio
    async def test_get_offloads_cache_file_operations(
        self,
        cache: ConcreteDailyCache,
        run_to_thread_inline: Mock,
    ) -> None:
        """缓存校验、保存和加载均通过 to_thread 执行。"""
        cache.fetch_fresh_mock.return_value = {"fresh": "data"}

        await cache.get()
        await cache.get()

        assert run_to_thread_inline.call_args_list == [
            ((cache.is_cache_valid,), {}),
            ((cache.save_cache, {"fresh": "data"}), {}),
            ((cache.is_cache_valid,), {}),
            ((cache.load_cache,), {}),
        ]

    @patch("app.services.daily_cache.today_business")
    @pytest.mark.asyncio
    async def test_get_no_cache_fetches_fresh(
        self, mock_today: AsyncMock, cache: ConcreteDailyCache, cache_dir: Path
    ) -> None:
        """验证无缓存时 get() 获取新数据。"""
        mock_today.return_value = date(2026, 2, 5)

        # 设置 fetch_fresh 返回新数据
        fresh_data = {"fresh": "data"}
        cache.fetch_fresh_mock.return_value = fresh_data

        result = await cache.get()

        # 应该返回新数据
        assert result == fresh_data
        # 应该调用 fetch_fresh
        cache.fetch_fresh_mock.assert_called_once()

        # 验证新数据已保存到缓存
        cache_file = cache_dir / "test.json"
        assert cache_file.exists()
        with cache_file.open("r", encoding="utf-8") as f:
            cache_data = json.load(f)
        assert cache_data["data"] == fresh_data
        assert cache_data["date"] == "2026-02-05"

    def test_cache_dir_created_on_init(self, tmp_path: Path, logger: logging.Logger) -> None:
        """验证初始化时创建缓存目录。"""
        cache_dir = tmp_path / "new_cache_dir"
        assert not cache_dir.exists()

        ConcreteDailyCache("test", cache_dir, logger)

        # 缓存目录应该被创建
        assert cache_dir.exists()
        assert cache_dir.is_dir()

    def test_is_cache_valid_non_dict_format(self, cache: ConcreteDailyCache, cache_dir: Path) -> None:
        """验证文件包含非字典数据时缓存无效。"""
        # 创建包含非 dict 数据的缓存文件
        cache_file = cache_dir / "test.json"
        cache_file.write_text(json.dumps(["list", "data"]))

        # 应该返回 False（非 dict 格式）
        assert cache.is_cache_valid() is False

    def test_load_cache_non_dict_format(self, cache: ConcreteDailyCache, cache_dir: Path) -> None:
        """验证文件包含非字典数据时 load_cache 返回 None。"""
        # 创建包含非 dict 数据的缓存文件
        cache_file = cache_dir / "test.json"
        cache_file.write_text(json.dumps("string data"))

        # 应该返回 None（非 dict 格式）
        result = cache.load_cache()
        assert result is None

    @patch("app.services.daily_cache.today_business")
    @pytest.mark.asyncio
    async def test_get_fallback_when_fetch_raises_exception(
        self, mock_today: AsyncMock, cache: ConcreteDailyCache, cache_dir: Path
    ) -> None:
        """验证 fetch_fresh 抛出异常时 get() 降级使用过期缓存。"""
        mock_today.return_value = date(2026, 2, 6)

        # 创建过期缓存
        cache_file = cache_dir / "test.json"
        stale_data = {"stale": "data"}
        cache_file.write_text(json.dumps({"date": "2026-02-05", "data": stale_data, "fetched_at": 1738713600000}))

        # 设置 fetch_fresh 抛出异常
        cache.fetch_fresh_mock.side_effect = RuntimeError("Network error")

        result = await cache.get()

        # 应该返回过期缓存（降级策略）
        assert result == stale_data
        # 应该调用 fetch_fresh
        cache.fetch_fresh_mock.assert_called_once()
