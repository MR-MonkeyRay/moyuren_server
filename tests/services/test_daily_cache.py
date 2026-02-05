"""Tests for app/services/daily_cache.py - daily cache service."""

import json
import logging
from datetime import date
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.services.daily_cache import DailyCache


class ConcreteDailyCache(DailyCache[dict[str, Any]]):
    """Concrete implementation for testing."""

    def __init__(self, namespace: str, cache_dir: Path, logger: logging.Logger):
        super().__init__(namespace, cache_dir, logger)
        self.fetch_fresh_mock = AsyncMock(return_value={"key": "value"})

    async def fetch_fresh(self) -> dict[str, Any] | None:
        return await self.fetch_fresh_mock()


class TestDailyCache:
    """Tests for DailyCache class."""

    @pytest.fixture
    def cache_dir(self, tmp_path: Path) -> Path:
        """Create temporary cache directory."""
        cache_dir = tmp_path / "daily"
        cache_dir.mkdir()
        return cache_dir

    @pytest.fixture
    def cache(self, cache_dir: Path, logger: logging.Logger) -> ConcreteDailyCache:
        """Create a ConcreteDailyCache instance."""
        return ConcreteDailyCache("test", cache_dir, logger)

    def test_cache_file_path(self, cache: ConcreteDailyCache, cache_dir: Path) -> None:
        """Test cache file path is correct."""
        expected_path = cache_dir / "test.json"
        assert cache._get_cache_file() == expected_path

    def test_is_cache_valid_no_file(self, cache: ConcreteDailyCache) -> None:
        """Test cache is invalid when file does not exist."""
        assert cache.is_cache_valid() is False

    @patch("app.services.daily_cache.today_business")
    def test_is_cache_valid_same_day(
        self, mock_today: AsyncMock, cache: ConcreteDailyCache, cache_dir: Path
    ) -> None:
        """Test cache is valid when date matches today."""
        mock_today.return_value = date(2026, 2, 5)

        # 创建缓存文件
        cache_file = cache_dir / "test.json"
        cache_file.write_text(json.dumps({
            "date": "2026-02-05",
            "data": {"key": "value"},
            "fetched_at": 1738713600000
        }))

        assert cache.is_cache_valid() is True

    @patch("app.services.daily_cache.today_business")
    def test_is_cache_valid_different_day(
        self, mock_today: AsyncMock, cache: ConcreteDailyCache, cache_dir: Path
    ) -> None:
        """Test cache is invalid when date does not match today."""
        mock_today.return_value = date(2026, 2, 6)

        # 创建缓存文件（昨天的日期）
        cache_file = cache_dir / "test.json"
        cache_file.write_text(json.dumps({
            "date": "2026-02-05",
            "data": {"key": "value"},
            "fetched_at": 1738713600000
        }))

        assert cache.is_cache_valid() is False

    def test_load_cache_success(self, cache: ConcreteDailyCache, cache_dir: Path) -> None:
        """Test successfully loading cache data."""
        # 创建缓存文件
        cache_file = cache_dir / "test.json"
        expected_data = {"key": "value", "nested": {"foo": "bar"}}
        cache_file.write_text(json.dumps({
            "date": "2026-02-05",
            "data": expected_data,
            "fetched_at": 1738713600000
        }))

        result = cache.load_cache()
        assert result == expected_data

    def test_load_cache_no_file(self, cache: ConcreteDailyCache) -> None:
        """Test loading cache returns None when file does not exist."""
        result = cache.load_cache()
        assert result is None

    def test_load_cache_invalid_json(
        self, cache: ConcreteDailyCache, cache_dir: Path
    ) -> None:
        """Test loading cache returns None when JSON is invalid."""
        cache_file = cache_dir / "test.json"
        cache_file.write_text("invalid json{")

        result = cache.load_cache()
        assert result is None

    @patch("app.services.daily_cache.today_business")
    def test_save_cache(
        self, mock_today: AsyncMock, cache: ConcreteDailyCache, cache_dir: Path
    ) -> None:
        """Test saving cache data (atomic write)."""
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
        """Test get() uses valid cache without fetching fresh data."""
        mock_today.return_value = date(2026, 2, 5)

        # 创建有效缓存
        cache_file = cache_dir / "test.json"
        cached_data = {"cached": "data"}
        cache_file.write_text(json.dumps({
            "date": "2026-02-05",
            "data": cached_data,
            "fetched_at": 1738713600000
        }))

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
        """Test get() fetches fresh data when cache is expired."""
        mock_today.return_value = date(2026, 2, 6)

        # 创建过期缓存（昨天的日期）
        cache_file = cache_dir / "test.json"
        cache_file.write_text(json.dumps({
            "date": "2026-02-05",
            "data": {"old": "data"},
            "fetched_at": 1738713600000
        }))

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
        """Test get() falls back to stale cache when fresh fetch fails."""
        mock_today.return_value = date(2026, 2, 6)

        # 创建过期缓存
        cache_file = cache_dir / "test.json"
        stale_data = {"stale": "data"}
        cache_file.write_text(json.dumps({
            "date": "2026-02-05",
            "data": stale_data,
            "fetched_at": 1738713600000
        }))

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
        """Test get() returns None when no cache exists and fetch fails."""
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
    async def test_get_force_refresh(
        self, mock_today: AsyncMock, cache: ConcreteDailyCache, cache_dir: Path
    ) -> None:
        """Test get() with force_refresh=True ignores valid cache."""
        mock_today.return_value = date(2026, 2, 5)

        # 创建有效缓存
        cache_file = cache_dir / "test.json"
        cache_file.write_text(json.dumps({
            "date": "2026-02-05",
            "data": {"cached": "data"},
            "fetched_at": 1738713600000
        }))

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

    @patch("app.services.daily_cache.today_business")
    @pytest.mark.asyncio
    async def test_get_no_cache_fetches_fresh(
        self, mock_today: AsyncMock, cache: ConcreteDailyCache, cache_dir: Path
    ) -> None:
        """Test get() fetches fresh data when no cache exists."""
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
        """Test cache directory is created on initialization."""
        cache_dir = tmp_path / "new_cache_dir"
        assert not cache_dir.exists()

        cache = ConcreteDailyCache("test", cache_dir, logger)

        # 缓存目录应该被创建
        assert cache_dir.exists()
        assert cache_dir.is_dir()

    def test_is_cache_valid_non_dict_format(
        self, cache: ConcreteDailyCache, cache_dir: Path
    ) -> None:
        """Test cache is invalid when file contains non-dict data."""
        # 创建包含非 dict 数据的缓存文件
        cache_file = cache_dir / "test.json"
        cache_file.write_text(json.dumps(["list", "data"]))

        # 应该返回 False（非 dict 格式）
        assert cache.is_cache_valid() is False

    def test_load_cache_non_dict_format(
        self, cache: ConcreteDailyCache, cache_dir: Path
    ) -> None:
        """Test load_cache returns None when file contains non-dict data."""
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
        """Test get() falls back to stale cache when fetch_fresh raises exception."""
        mock_today.return_value = date(2026, 2, 6)

        # 创建过期缓存
        cache_file = cache_dir / "test.json"
        stale_data = {"stale": "data"}
        cache_file.write_text(json.dumps({
            "date": "2026-02-05",
            "data": stale_data,
            "fetched_at": 1738713600000
        }))

        # 设置 fetch_fresh 抛出异常
        cache.fetch_fresh_mock.side_effect = RuntimeError("Network error")

        result = await cache.get()

        # 应该返回过期缓存（降级策略）
        assert result == stale_data
        # 应该调用 fetch_fresh
        cache.fetch_fresh_mock.assert_called_once()
