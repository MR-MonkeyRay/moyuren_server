"""app/services/holiday.py 节假日服务测试。"""

import json
import logging
import os
import time
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import respx
from httpx import AsyncClient, Response

from app.services.holiday import CachedHolidayService, HolidayService


class TestHolidayService:
    """HolidayService 类测试。"""

    @pytest.fixture
    def cache_dir(self, tmp_path: Path) -> Path:
        """创建临时缓存目录。"""
        cache_dir = tmp_path / "holidays"
        cache_dir.mkdir()
        return cache_dir

    @pytest.fixture
    def service(self, cache_dir: Path, logger: logging.Logger) -> HolidayService:
        """创建 HolidayService 实例。"""
        return HolidayService(
            logger=logger, cache_dir=cache_dir, ghproxy_urls=["https://mirror.example.com/"], timeout_sec=5
        )

    @pytest.fixture
    def sample_holiday_data(self) -> dict[str, Any]:
        """一年的节假日样例数据。"""
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
        self, tmp_path: Path, cache_dir: Path, logger: logging.Logger,
        monkeypatch: pytest.MonkeyPatch,
    ) -> CachedHolidayService:
        """创建带日级聚合缓存的节假日服务。"""
        async def inline_io(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
            return func(*args, **kwargs)

        monkeypatch.setattr("app.services.daily_cache.asyncio.to_thread", inline_io)
        return CachedHolidayService(
            logger=logger,
            cache_dir=tmp_path / "daily",
            raw_cache_dir=cache_dir,
            ghproxy_urls=["https://mirror.example.com/"],
            timeout_sec=5,
        )

    def test_build_urls_with_mirrors(self, service: HolidayService) -> None:
        """测试配置镜像 URL 时的 URL 构建。"""
        urls = service._build_urls(2026)

        assert len(urls) == 2
        assert "mirror.example.com" in urls[0]
        assert "raw.githubusercontent.com" in urls[1]

    def test_build_urls_without_mirrors(self, cache_dir: Path, logger: logging.Logger) -> None:
        """测试未配置镜像 URL 时的 URL 构建。"""
        service = HolidayService(logger=logger, cache_dir=cache_dir)
        urls = service._build_urls(2026)

        assert len(urls) == 1
        assert "raw.githubusercontent.com" in urls[0]

    def test_build_urls_skips_invalid_mirrors(self, cache_dir: Path, logger: logging.Logger) -> None:
        """测试构建 URL 时跳过无效镜像 URL。"""
        service = HolidayService(
            logger=logger, cache_dir=cache_dir, ghproxy_urls=["invalid-no-protocol", "https://valid.com/"]
        )
        urls = service._build_urls(2026)

        assert len(urls) == 2
        assert "valid.com" in urls[0]
        assert "raw.githubusercontent.com" in urls[1]

    def test_get_ttl_past_year(self, service: HolidayService) -> None:
        """测试往年数据的 TTL 为 None（永久有效）。"""
        with patch.object(service, "_get_today", return_value=date(2026, 2, 4)):
            ttl = service._get_ttl(2025)
            assert ttl is None

    def test_get_ttl_current_year(self, service: HolidayService) -> None:
        """测试当年数据的 TTL 为 7 天。"""
        with patch.object(service, "_get_today", return_value=date(2026, 2, 4)):
            ttl = service._get_ttl(2026)
            assert ttl == 7 * 24 * 3600

    def test_get_ttl_next_year(self, service: HolidayService) -> None:
        """测试次年数据的 TTL 为 12 小时。"""
        with patch.object(service, "_get_today", return_value=date(2026, 2, 4)):
            ttl = service._get_ttl(2027)
            assert ttl == 12 * 3600

    def test_is_cache_valid_no_file(self, service: HolidayService) -> None:
        """测试文件不存在时缓存无效。"""
        assert service._is_cache_valid(2026) is False

    def test_is_cache_valid_past_year(
        self, service: HolidayService, cache_dir: Path, sample_holiday_data: dict
    ) -> None:
        """测试往年缓存只要存在就始终有效。"""
        cache_file = cache_dir / "2025.json"
        cache_file.write_text(json.dumps(sample_holiday_data))

        with patch.object(service, "_get_today", return_value=date(2026, 2, 4)):
            assert service._is_cache_valid(2025) is True

    def test_is_cache_valid_current_year_fresh(
        self, service: HolidayService, cache_dir: Path, sample_holiday_data: dict
    ) -> None:
        """测试当年未过期的缓存有效。"""
        cache_file = cache_dir / "2026.json"
        cache_file.write_text(json.dumps(sample_holiday_data))

        with patch.object(service, "_get_today", return_value=date(2026, 2, 4)):
            assert service._is_cache_valid(2026) is True

    def test_is_cache_valid_current_year_expired(
        self, service: HolidayService, cache_dir: Path, sample_holiday_data: dict
    ) -> None:
        """测试当年已过期的缓存无效。"""
        cache_file = cache_dir / "2026.json"
        cache_file.write_text(json.dumps(sample_holiday_data))

        # 将文件时间设为 8 天前
        old_time = time.time() - (8 * 24 * 3600)
        os.utime(cache_file, (old_time, old_time))

        with patch.object(service, "_get_today", return_value=date(2026, 2, 4)):
            assert service._is_cache_valid(2026) is False

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_holidays_success(self, service: HolidayService, sample_holiday_data: dict) -> None:
        """测试成功获取节假日数据。"""
        # 使用正则表达式 mock 所有年份的接口
        for year in [2025, 2026, 2027]:
            data = sample_holiday_data.copy()
            data["year"] = year
            respx.get(url__regex=rf".*{year}\.json$").mock(return_value=Response(200, json=data))

        with patch.object(service, "_get_today", return_value=date(2026, 2, 4)):
            result = await service.fetch_holidays()

        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_fetch_holidays_reuses_injected_client_with_service_timeout(
        self,
        cache_dir: Path,
        logger: logging.Logger,
        sample_holiday_data: dict[str, Any],
    ) -> None:
        """注入的客户端保持打开，每次请求仍使用服务配置的超时时间。"""
        response = MagicMock()
        response.json.return_value = sample_holiday_data
        response.raise_for_status.return_value = None
        client = MagicMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=response)
        client.aclose = AsyncMock()
        service = HolidayService(
            logger=logger, cache_dir=cache_dir, timeout_sec=5, http_client=client
        )

        with patch.object(service, "_get_today", return_value=date(2026, 2, 4)):
            result = await service.fetch_holidays()

        assert isinstance(result, list)
        assert client.get.await_count == 3
        assert all(
            call.kwargs["timeout"] == httpx.Timeout(5)
            for call in client.get.await_args_list
        )
        client.aclose.assert_not_awaited()

    def test_cached_service_forwards_injected_client(
        self, tmp_path: Path, cache_dir: Path, logger: logging.Logger
    ) -> None:
        """缓存包装层将共享客户端传给内部网络服务。"""
        client = MagicMock(spec=httpx.AsyncClient)

        service = CachedHolidayService(
            logger=logger,
            cache_dir=tmp_path / "daily",
            raw_cache_dir=cache_dir,
            http_client=client,
        )

        assert service._service._http_client is client

    @respx.mock
    @pytest.mark.asyncio
    async def test_get_preserves_stale_cache_when_all_years_fail(
        self, cached_service: CachedHolidayService, cache_dir: Path
    ) -> None:
        """测试全部获取失败时保留过期的聚合缓存。"""
        cache_file = cached_service.cache_dir / "holidays.json"
        stale_data = [{"name": "春节", "start_date": "2026-02-15"}]
        original = json.dumps({
            "date": "2026-02-03",
            "data": {"holidays": stale_data, "make_up_workdays": []},
        })
        cache_file.write_text(original, encoding="utf-8")
        respx.get(url__regex=r".*\.json$").mock(return_value=Response(500))

        with (
            patch.object(HolidayService, "_get_today", return_value=date(2026, 2, 4)),
            patch("app.services.daily_cache.today_business", return_value=date(2026, 2, 4)),
        ):
            assert await cached_service.fetch_fresh() is None
            result = await cached_service.get()

        assert result == {"holidays": stale_data, "make_up_workdays": []}
        assert cache_file.read_text(encoding="utf-8") == original
        assert list(cache_dir.iterdir()) == []

    @pytest.mark.asyncio
    async def test_fetch_holidays_returns_none_for_year_exceptions(
        self, service: HolidayService
    ) -> None:
        """测试汇总的各年份异常表示全部获取失败。"""
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
        """测试部分获取成功时缓存列表，包括正常的空结果。"""
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

        assert isinstance(result, dict)
        assert result["make_up_workdays"] == []
        holidays = result["holidays"]
        if holiday_date == "2026-02-15":
            assert len(holidays) == 1
            assert holidays[0]["start_date"] == holiday_date
        else:
            assert holidays == []
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
        """测试无效镜像数据不会被缓存，并会尝试下一个 URL。"""
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
        """测试读取原始缓存时拒绝无效的年度数据。"""
        (cache_dir / "2026.json").write_text(json.dumps(invalid_data), encoding="utf-8")

        assert service._load_from_cache(2026) is None

    def test_save_to_cache_atomic_success(
        self, service: HolidayService, cache_dir: Path, sample_holiday_data: dict[str, Any]
    ) -> None:
        """测试原子写入成功后只留下目标文件。"""
        cache_file = cache_dir / "2026.json"
        cache_file.write_text('{"days": []}', encoding="utf-8")

        service._save_to_cache(2026, sample_holiday_data)

        assert service._load_from_cache(2026) == sample_holiday_data
        assert list(cache_dir.iterdir()) == [cache_file]

    @pytest.mark.parametrize("error", [TypeError("dump failed"), OSError("write failed")])
    def test_save_to_cache_preserves_original_on_dump_failure(
        self, service: HolidayService, cache_dir: Path, error: Exception
    ) -> None:
        """测试 JSON 写入失败时保留原文件并删除临时文件。"""
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
        """测试获取数据时使用有效缓存。"""
        # 创建缓存文件
        for year in [2025, 2026, 2027]:
            data = sample_holiday_data.copy()
            data["year"] = year
            cache_file = cache_dir / f"{year}.json"
            cache_file.write_text(json.dumps(data))

        with patch.object(service, "_get_today", return_value=date(2026, 2, 4)):
            result = await service.fetch_holidays()

        # 缓存有效，不应发起任何 HTTP 请求
        assert respx.calls.call_count == 0
        assert isinstance(result, list)

    @pytest.mark.parametrize("today", [date(2026, 2, 4), date(2026, 2, 14), date(2026, 2, 15)])
    def test_make_up_workdays_do_not_change_display_list(
        self, service: HolidayService, sample_holiday_data: dict[str, Any], today: date
    ) -> None:
        """单独保存未来补班日，保留节假日分组和当天的补班行。"""
        sample_holiday_data["days"].extend([
            {"name": "元旦", "date": "2026-01-04", "isOffDay": False},
            {"name": "元旦", "date": "2027-01-03", "isOffDay": False},
            {"name": "元旦", "date": "2027-01-02", "isOffDay": False},
            {"name": "元旦", "date": "2027-01-02", "isOffDay": False},
            {"date": "invalid", "isOffDay": False},
            {"date": None, "isOffDay": False},
        ])
        with patch.object(service, "_get_today", return_value=today):
            result = service._merge_and_process([sample_holiday_data])

        expected = [{
            "name": "春节", "start_date": "2026-02-15", "end_date": "2026-02-21",
            "duration": 7, "days_left": max(0, (date(2026, 2, 15) - today).days),
            "color": None, "is_off_day": True,
        }]
        if today == date(2026, 2, 14):
            expected.insert(0, {
                "name": "春节（补班）", "start_date": "2026-02-14", "end_date": "2026-02-14",
                "duration": 1, "days_left": 0, "color": None, "is_off_day": False,
            })
        assert result == expected
        assert service.make_up_workdays == (
            (["2026-02-14"] if today <= date(2026, 2, 14) else [])
            + ["2027-01-02", "2027-01-03"]
        )

    @pytest.mark.asyncio
    async def test_make_up_workdays_survive_daily_cache_round_trip(
        self, cached_service: CachedHolidayService, sample_holiday_data: dict[str, Any]
    ) -> None:
        """新获取和缓存的数据在节假日列表之外携带相同的未来补班日。"""
        with (
            patch.object(HolidayService, "_get_today", return_value=date(2026, 2, 4)),
            patch("app.services.daily_cache.today_business", return_value=date(2026, 2, 4)),
            patch.object(cached_service._service, "_fetch_year_data",
                         new=AsyncMock(side_effect=[None, sample_holiday_data, None])),
        ):
            result = await cached_service.get()
            assert result is not None
            assert result["make_up_workdays"] == ["2026-02-14"]
            assert cached_service.load_cache() == result

    @pytest.mark.asyncio
    async def test_obsolete_daily_cache_refreshes_make_up_workdays(
        self, cached_service: CachedHolidayService
    ) -> None:
        """仅含列表的缓存无法提供补班日，必须刷新。"""
        holidays = [{"name": "春节", "start_date": "2026-02-15"}]
        (cached_service.cache_dir / "holidays.json").write_text(json.dumps({
            "date": cached_service.cache_key(), "data": holidays,
        }), encoding="utf-8")
        fresh = {"holidays": holidays, "make_up_workdays": ["2026-02-14"]}
        with (
            patch.object(cached_service._service, "_get_today", return_value=date(2026, 2, 4)),
            patch.object(cached_service, "fetch_fresh", new=AsyncMock(return_value=fresh)),
        ):
            assert await cached_service.get() == fresh
            assert cached_service.load_cache() == fresh

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_holidays_fallback_to_cache_on_error(
        self, service: HolidayService, cache_dir: Path, sample_holiday_data: dict
    ) -> None:
        """测试网络失败时回退到过期缓存。"""
        # 创建过期缓存
        cache_file = cache_dir / "2026.json"
        cache_file.write_text(json.dumps(sample_holiday_data))
        old_time = time.time() - (8 * 24 * 3600)
        os.utime(cache_file, (old_time, old_time))

        # 使用正则表达式 mock 网络失败
        respx.get(url__regex=r".*\.json$").mock(return_value=Response(500))

        with patch.object(service, "_get_today", return_value=date(2026, 2, 4)):
            result = await service.fetch_holidays()

        # 仍应返回过期缓存中的数据
        assert isinstance(result, list)

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_holidays_mirror_fallback(self, service: HolidayService, sample_holiday_data: dict) -> None:
        """测试镜像失败时回退到 GitHub。"""
        # 使用正则表达式 mock 镜像失败、GitHub 成功
        respx.get(url__regex=r".*mirror\.example\.com.*").mock(return_value=Response(500))
        respx.get(url__regex=r".*raw\.githubusercontent\.com.*").mock(
            return_value=Response(200, json=sample_holiday_data)
        )

        with patch.object(service, "_get_today", return_value=date(2026, 2, 4)):
            result = await service.fetch_holidays()

        assert isinstance(result, list)
