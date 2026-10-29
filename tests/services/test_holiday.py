"""Tests for app/services/holiday.py - holiday service."""

import json
import logging
import os
import time
from datetime import date
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
import respx
from httpx import AsyncClient, Response

from app.services.holiday import CachedHolidayService, HolidayService


class TestHolidayService:
    """Tests for HolidayService class."""

    @pytest.fixture
    def cache_dir(self, tmp_path: Path) -> Path:
        """Create a temporary cache directory."""
        cache_dir = tmp_path / "holidays"
        cache_dir.mkdir()
        return cache_dir

    @pytest.fixture
    def service(self, cache_dir: Path, logger: logging.Logger) -> HolidayService:
        """Create a HolidayService instance."""
        return HolidayService(
            logger=logger, cache_dir=cache_dir, ghproxy_urls=["https://mirror.example.com/"], timeout_sec=5
        )

    @pytest.fixture
    def sample_holiday_data(self) -> dict[str, Any]:
        """Sample holiday data for a year."""
        return {
            "$schema": "https://raw.githubusercontent.com/NateScarlet/holiday-cn/master/schema.json",
            "$id": "https://raw.githubusercontent.com/NateScarlet/holiday-cn/master/2026.json",
            "year": 2026,
            "papers": [],
            "days": [
                {"name": "元旦", "date": "2026-01-01", "isOffDay": True},
                {"name": "春节", "date": "2026-02-15", "isOffDay": True},
                {"name": "春节", "date": "2026-02-16", "isOffDay": True},
                {"name": "春节", "date": "2026-02-17", "isOffDay": True},
                {"name": "春节", "date": "2026-02-18", "isOffDay": True},
                {"name": "春节", "date": "2026-02-19", "isOffDay": True},
                {"name": "春节", "date": "2026-02-20", "isOffDay": True},
                {"name": "春节", "date": "2026-02-21", "isOffDay": True},
                {"name": "春节", "date": "2026-02-14", "isOffDay": False},  # 补班
            ],
        }

    @pytest.fixture
    def cached_service(
        self, tmp_path: Path, cache_dir: Path, logger: logging.Logger
    ) -> CachedHolidayService:
        """Create a holiday service with a daily aggregate cache."""
        return CachedHolidayService(
            logger=logger,
            cache_dir=tmp_path / "daily",
            raw_cache_dir=cache_dir,
            ghproxy_urls=["https://mirror.example.com/"],
            timeout_sec=5,
        )

    def test_build_urls_with_mirrors(self, service: HolidayService) -> None:
        """Test URL building with mirror URLs."""
        urls = service._build_urls(2026)

        assert len(urls) == 2
        assert "mirror.example.com" in urls[0]
        assert "raw.githubusercontent.com" in urls[1]

    def test_build_urls_without_mirrors(self, cache_dir: Path, logger: logging.Logger) -> None:
        """Test URL building without mirror URLs."""
        service = HolidayService(logger=logger, cache_dir=cache_dir)
        urls = service._build_urls(2026)

        assert len(urls) == 1
        assert "raw.githubusercontent.com" in urls[0]

    def test_build_urls_skips_invalid_mirrors(self, cache_dir: Path, logger: logging.Logger) -> None:
        """Test URL building skips invalid mirror URLs."""
        service = HolidayService(
            logger=logger, cache_dir=cache_dir, ghproxy_urls=["invalid-no-protocol", "https://valid.com/"]
        )
        urls = service._build_urls(2026)

        assert len(urls) == 2
        assert "valid.com" in urls[0]
        assert "raw.githubusercontent.com" in urls[1]

    def test_get_ttl_past_year(self, service: HolidayService) -> None:
        """Test TTL for past year is None (permanent)."""
        with patch.object(service, "_get_today", return_value=date(2026, 2, 4)):
            ttl = service._get_ttl(2025)
            assert ttl is None

    def test_get_ttl_current_year(self, service: HolidayService) -> None:
        """Test TTL for current year is 7 days."""
        with patch.object(service, "_get_today", return_value=date(2026, 2, 4)):
            ttl = service._get_ttl(2026)
            assert ttl == 7 * 24 * 3600

    def test_get_ttl_next_year(self, service: HolidayService) -> None:
        """Test TTL for next year is 12 hours."""
        with patch.object(service, "_get_today", return_value=date(2026, 2, 4)):
            ttl = service._get_ttl(2027)
            assert ttl == 12 * 3600

    def test_is_cache_valid_no_file(self, service: HolidayService) -> None:
        """Test cache validity when file doesn't exist."""
        assert service._is_cache_valid(2026) is False

    def test_is_cache_valid_past_year(
        self, service: HolidayService, cache_dir: Path, sample_holiday_data: dict
    ) -> None:
        """Test cache validity for past year (always valid if exists)."""
        cache_file = cache_dir / "2025.json"
        cache_file.write_text(json.dumps(sample_holiday_data))

        with patch.object(service, "_get_today", return_value=date(2026, 2, 4)):
            assert service._is_cache_valid(2025) is True

    def test_is_cache_valid_current_year_fresh(
        self, service: HolidayService, cache_dir: Path, sample_holiday_data: dict
    ) -> None:
        """Test cache validity for current year with fresh cache."""
        cache_file = cache_dir / "2026.json"
        cache_file.write_text(json.dumps(sample_holiday_data))

        with patch.object(service, "_get_today", return_value=date(2026, 2, 4)):
            assert service._is_cache_valid(2026) is True

    def test_is_cache_valid_current_year_expired(
        self, service: HolidayService, cache_dir: Path, sample_holiday_data: dict
    ) -> None:
        """Test cache validity for current year with expired cache."""
        cache_file = cache_dir / "2026.json"
        cache_file.write_text(json.dumps(sample_holiday_data))

        # Make file old (8 days ago)
        old_time = time.time() - (8 * 24 * 3600)
        os.utime(cache_file, (old_time, old_time))

        with patch.object(service, "_get_today", return_value=date(2026, 2, 4)):
            assert service._is_cache_valid(2026) is False

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_holidays_success(self, service: HolidayService, sample_holiday_data: dict) -> None:
        """Test successful holiday fetch."""
        # Mock all year endpoints using regex pattern
        for year in [2025, 2026, 2027]:
            data = sample_holiday_data.copy()
            data["year"] = year
            respx.get(url__regex=rf".*{year}\.json$").mock(return_value=Response(200, json=data))

        with patch.object(service, "_get_today", return_value=date(2026, 2, 4)):
            result = await service.fetch_holidays()

        assert isinstance(result, list)

    @respx.mock
    @pytest.mark.asyncio
    async def test_get_preserves_stale_cache_when_all_years_fail(
        self, cached_service: CachedHolidayService, cache_dir: Path
    ) -> None:
        """Test total fetch failure preserves the stale aggregate cache."""
        cache_file = cached_service.cache_dir / "holidays.json"
        stale_data = [{"name": "春节", "start_date": "2026-02-15"}]
        original = json.dumps({"date": "2026-02-03", "data": stale_data})
        cache_file.write_text(original, encoding="utf-8")
        respx.get(url__regex=r".*\.json$").mock(return_value=Response(500))

        with (
            patch.object(HolidayService, "_get_today", return_value=date(2026, 2, 4)),
            patch("app.services.daily_cache.today_business", return_value=date(2026, 2, 4)),
        ):
            assert await cached_service.fetch_fresh() is None
            result = await cached_service.get()

        assert result == stale_data
        assert cache_file.read_text(encoding="utf-8") == original
        assert list(cache_dir.iterdir()) == []

    @pytest.mark.asyncio
    async def test_fetch_holidays_returns_none_for_year_exceptions(
        self, service: HolidayService
    ) -> None:
        """Test gathered year exceptions signal total failure."""
        with patch.object(
            service, "_fetch_year_data", new=AsyncMock(side_effect=RuntimeError("fetch failed"))
        ):
            assert await service.fetch_holidays() is None

    @respx.mock
    @pytest.mark.asyncio
    @pytest.mark.parametrize("holiday_date", ["2026-02-15", "2026-01-01", None])
    async def test_get_caches_one_valid_year(
        self, cached_service: CachedHolidayService, holiday_date: str | None
    ) -> None:
        """Test partial success caches lists, including legitimately empty results."""
        days = (
            [{"name": "假期", "date": holiday_date, "isOffDay": True}]
            if holiday_date is not None else []
        )
        respx.get(url__regex=r".*202[57]\.json$").mock(return_value=Response(500))
        respx.get(url__regex=r".*2026\.json$").mock(
            return_value=Response(200, json={"days": days})
        )

        with (
            patch.object(HolidayService, "_get_today", return_value=date(2026, 2, 4)),
            patch("app.services.daily_cache.today_business", return_value=date(2026, 2, 4)),
        ):
            result = await cached_service.get()

        assert isinstance(result, list)
        if holiday_date == "2026-02-15":
            assert len(result) == 1
            assert result[0]["start_date"] == holiday_date
        else:
            assert result == []
        cached_data = json.loads(
            (cached_service.cache_dir / "holidays.json").read_text(encoding="utf-8")
        )
        assert cached_data["data"] == result
        assert cached_data["date"] == "2026-02-04"

    @respx.mock
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "invalid_data", [None, [], {"foo": 1}, {"days": {}}, {"days": [None]}, {"days": [{}, "bad"]}]
    )
    async def test_fetch_year_data_skips_invalid_mirror_payload(
        self, service: HolidayService, invalid_data: Any, sample_holiday_data: dict[str, Any]
    ) -> None:
        """Test invalid mirror payloads are never cached and the next URL is tried."""
        mirror_url, raw_url = service._build_urls(2026)
        mirror = respx.get(mirror_url).mock(
            return_value=Response(200, content=json.dumps(invalid_data))
        )
        raw = respx.get(raw_url).mock(return_value=Response(200, json=sample_holiday_data))

        with patch.object(service, "_save_to_cache", wraps=service._save_to_cache) as save:
            async with AsyncClient() as client:
                result = await service._fetch_year_data(client, 2026)

        assert result == sample_holiday_data
        assert mirror.called
        assert raw.called
        save.assert_called_once_with(2026, sample_holiday_data)
        assert service._load_from_cache(2026) == sample_holiday_data

    @pytest.mark.parametrize(
        "invalid_data", [None, [], {"foo": 1}, {"days": {}}, {"days": [None]}, {"days": [{}, "bad"]}]
    )
    def test_load_from_cache_rejects_invalid_shape(
        self, service: HolidayService, cache_dir: Path, invalid_data: Any
    ) -> None:
        """Test raw cache loading rejects invalid year payloads."""
        (cache_dir / "2026.json").write_text(json.dumps(invalid_data), encoding="utf-8")

        assert service._load_from_cache(2026) is None

    def test_save_to_cache_atomic_success(
        self, service: HolidayService, cache_dir: Path, sample_holiday_data: dict[str, Any]
    ) -> None:
        """Test a successful atomic write leaves only the destination file."""
        cache_file = cache_dir / "2026.json"
        cache_file.write_text('{"days": []}', encoding="utf-8")

        service._save_to_cache(2026, sample_holiday_data)

        assert service._load_from_cache(2026) == sample_holiday_data
        assert list(cache_dir.iterdir()) == [cache_file]

    @pytest.mark.parametrize("error", [TypeError("dump failed"), OSError("write failed")])
    def test_save_to_cache_preserves_original_on_dump_failure(
        self, service: HolidayService, cache_dir: Path, error: Exception
    ) -> None:
        """Test failed JSON writes preserve the original and remove temporary files."""
        cache_file = cache_dir / "2026.json"
        original = '{"days": []}'
        cache_file.write_text(original, encoding="utf-8")

        with patch("app.services.holiday.json.dump", side_effect=error):
            service._save_to_cache(2026, {"days": []})

        assert cache_file.read_text(encoding="utf-8") == original
        assert list(cache_dir.iterdir()) == [cache_file]

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_holidays_uses_cache(
        self, service: HolidayService, cache_dir: Path, sample_holiday_data: dict
    ) -> None:
        """Test fetch uses valid cache."""
        # Create cache files
        for year in [2025, 2026, 2027]:
            data = sample_holiday_data.copy()
            data["year"] = year
            cache_file = cache_dir / f"{year}.json"
            cache_file.write_text(json.dumps(data))

        with patch.object(service, "_get_today", return_value=date(2026, 2, 4)):
            result = await service.fetch_holidays()

        # Should not make any HTTP requests (cache is valid)
        assert respx.calls.call_count == 0
        assert isinstance(result, list)

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_holidays_fallback_to_cache_on_error(
        self, service: HolidayService, cache_dir: Path, sample_holiday_data: dict
    ) -> None:
        """Test fallback to expired cache when network fails."""
        # Create expired cache
        cache_file = cache_dir / "2026.json"
        cache_file.write_text(json.dumps(sample_holiday_data))
        old_time = time.time() - (8 * 24 * 3600)
        os.utime(cache_file, (old_time, old_time))

        # Mock network failure using regex
        respx.get(url__regex=r".*\.json$").mock(return_value=Response(500))

        with patch.object(service, "_get_today", return_value=date(2026, 2, 4)):
            result = await service.fetch_holidays()

        # Should still return data from expired cache
        assert isinstance(result, list)

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_holidays_mirror_fallback(self, service: HolidayService, sample_holiday_data: dict) -> None:
        """Test fallback to GitHub when mirror fails."""
        # Mock mirror failure, GitHub success using regex
        respx.get(url__regex=r".*mirror\.example\.com.*").mock(return_value=Response(500))
        respx.get(url__regex=r".*raw\.githubusercontent\.com.*").mock(
            return_value=Response(200, json=sample_holiday_data)
        )

        with patch.object(service, "_get_today", return_value=date(2026, 2, 4)):
            result = await service.fetch_holidays()

        assert isinstance(result, list)
