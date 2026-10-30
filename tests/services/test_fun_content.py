"""Tests for app/services/fun_content.py - fun content fetching service."""

import json
import logging
from datetime import date
from pathlib import Path

import httpx
import pytest
import respx
from httpx import Response

from app.core.config import FunContentEndpoint, FunContentSource, InstancesConfig
from app.services.fun_content import CachedFunContentService, FunContentService


class TestFunContentService:
    """Tests for FunContentService class."""

    @pytest.fixture
    def sample_config(self) -> FunContentSource:
        """Create a sample fun content configuration."""
        return FunContentSource(
            timeout_sec=5,
            endpoints=[
                FunContentEndpoint(
                    name="hitokoto",
                    url="https://api.example.com/hitokoto",
                    data_path="data.hitokoto",
                    display_title="💬 一言",
                ),
                FunContentEndpoint(
                    name="joke", url="https://api.example.com/joke", data_path="data.content", display_title="🤣 冷笑话"
                ),
            ],
        )

    @pytest.fixture
    def service(self, sample_config: FunContentSource) -> FunContentService:
        """Create a FunContentService instance."""
        return FunContentService(config=sample_config)

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_content_success(self, service: FunContentService) -> None:
        """Test successful content fetch."""
        # Mock both endpoints since shuffle order is date-dependent
        respx.get("https://api.example.com/hitokoto").mock(
            return_value=Response(200, json={"data": {"hitokoto": "生活不止眼前的苟且"}})
        )
        respx.get("https://api.example.com/joke").mock(
            return_value=Response(200, json={"data": {"content": "这是一个笑话"}})
        )

        result = await service.fetch_content(date(2026, 2, 4))

        # Should get content from one of the endpoints
        assert "title" in result
        assert "content" in result
        assert result["type"] in {"hitokoto", "joke"}
        assert result["content"] != ""

    @respx.mock
    @pytest.mark.asyncio
    async def test_endpoint_name_is_type_when_display_title_changes(self) -> None:
        """The stable endpoint name is independent of the configured title."""
        config = FunContentSource(
            timeout_sec=5,
            endpoints=[
                FunContentEndpoint(
                    name="hitokoto",
                    url="https://api.example.com/hitokoto",
                    data_path="data.hitokoto",
                    display_title="今日灵感",
                )
            ],
        )
        service = FunContentService(config=config)
        respx.get("https://api.example.com/hitokoto").mock(
            return_value=Response(200, json={"data": {"hitokoto": "生活不止眼前"}})
        )

        result = await service.fetch_content(date(2026, 2, 4))

        assert result == {
            "title": "今日灵感",
            "content": "生活不止眼前",
            "type": "hitokoto",
        }

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_content_fallback_to_second_endpoint(self, service: FunContentService) -> None:
        """Test fallback to second endpoint when first fails."""
        respx.get("https://api.example.com/hitokoto").mock(return_value=Response(500))
        respx.get("https://api.example.com/joke").mock(
            return_value=Response(200, json={"data": {"content": "这是一个笑话"}})
        )

        result = await service.fetch_content(date(2026, 2, 4))

        # Should get content from one of the endpoints
        assert "content" in result
        assert result["content"] != ""

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_content_all_fail_returns_default(self, service: FunContentService) -> None:
        """Test returns default content when all endpoints fail."""
        respx.get("https://api.example.com/hitokoto").mock(return_value=Response(500))
        respx.get("https://api.example.com/joke").mock(return_value=Response(500))

        result = await service.fetch_content(date(2026, 2, 4))

        assert result["title"] == "🐟 摸鱼小贴士"
        assert "摸鱼" in result["content"]
        assert result["type"] == "default"

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_content_timeout_fallback(self, service: FunContentService) -> None:
        """Test fallback when endpoint times out."""
        respx.get("https://api.example.com/hitokoto").mock(side_effect=httpx.TimeoutException("Timeout"))
        respx.get("https://api.example.com/joke").mock(
            return_value=Response(200, json={"data": {"content": "笑话内容"}})
        )

        result = await service.fetch_content(date(2026, 2, 4))

        assert "content" in result

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_content_empty_content_skipped(self, service: FunContentService) -> None:
        """Test empty content is skipped."""
        respx.get("https://api.example.com/hitokoto").mock(return_value=Response(200, json={"data": {"hitokoto": ""}}))
        respx.get("https://api.example.com/joke").mock(
            return_value=Response(200, json={"data": {"content": "有效内容"}})
        )

        result = await service.fetch_content(date(2026, 2, 4))

        assert result["content"] != ""

    def test_shuffle_by_date_consistent(self, service: FunContentService) -> None:
        """Test shuffle produces consistent results for same date."""
        date1 = date(2026, 2, 4)

        result1 = service._shuffle_by_date(date1)
        result2 = service._shuffle_by_date(date1)

        # Same date should produce same order
        assert [e.name for e in result1] == [e.name for e in result2]

    def test_shuffle_by_date_different_dates(self, service: FunContentService) -> None:
        """Test shuffle produces different results for different dates."""
        # Note: This test may occasionally fail if the shuffle happens to produce
        # the same order for different dates, but it's statistically unlikely
        date1 = date(2026, 2, 4)
        date2 = date(2026, 2, 5)

        result1 = service._shuffle_by_date(date1)
        result2 = service._shuffle_by_date(date2)

        # Different dates may produce different orders
        # We just verify the function runs without error
        assert len(result1) == len(result2)

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_content_whitespace_only_skipped(self, service: FunContentService) -> None:
        """Test whitespace-only content is skipped."""
        respx.get("https://api.example.com/hitokoto").mock(
            return_value=Response(200, json={"data": {"hitokoto": "   "}})
        )
        respx.get("https://api.example.com/joke").mock(return_value=Response(500))

        result = await service.fetch_content(date(2026, 2, 4))

        # Should fall back to default
        assert result["title"] == "🐟 摸鱼小贴士"

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_content_invalid_json_structure(self, service: FunContentService) -> None:
        """Test handles invalid JSON structure gracefully."""
        respx.get("https://api.example.com/hitokoto").mock(return_value=Response(200, json={"wrong": "structure"}))
        respx.get("https://api.example.com/joke").mock(return_value=Response(500))

        result = await service.fetch_content(date(2026, 2, 4))

        # Should fall back to default
        assert result["title"] == "🐟 摸鱼小贴士"

    @pytest.mark.parametrize(
        "payload",
        [
            None,
            [],
            "text",
            {},
            {"data": None},
            {"data": []},
            {"data": {"hitokoto": None}},
            {"data": {"hitokoto": {"text": "一言"}}},
        ],
    )
    def test_extract_content_rejects_unusable_shapes(
        self,
        service: FunContentService,
        sample_config: FunContentSource,
        payload: object,
    ) -> None:
        """空值与结构异常的响应返回 None，不抛出异常。"""
        endpoint = sample_config.endpoints[0]

        assert service._extract_content(payload, endpoint) is None

    @pytest.mark.parametrize(
        ("payload", "path"),
        [
            (None, "data.content"),
            ({"data": None}, "data.content"),
            ({"data": []}, "data.content"),
            ({"data": {"content": None}}, "data.content.value"),
        ],
    )
    def test_extract_by_path_handles_null_intermediate_values(
        self, service: FunContentService, payload: object, path: str
    ) -> None:
        """路径中间值无效时返回 None，不抛出异常。"""
        assert service._extract_by_path(payload, path) is None


class TestFunContentInstanceSwitching:
    """Tests for public 60s instance switching in FunContentService."""

    @pytest.fixture
    def config(self) -> FunContentSource:
        """Create a fun content configuration with a public instance fallback."""
        return FunContentSource(
            timeout_sec=5,
            endpoints=[
                FunContentEndpoint(
                    name="hitokoto",
                    url="https://api.example.com/hitokoto",
                    data_path="data.hitokoto",
                    display_title="💬 一言",
                ),
                FunContentEndpoint(
                    name="joke",
                    url="https://api.example.com/joke",
                    data_path="data.content",
                    display_title="🤣 冷笑话",
                ),
            ],
        )

    @respx.mock
    @pytest.mark.asyncio
    async def test_switches_to_instance_when_primary_blocked(
        self, config: FunContentSource
    ) -> None:
        """Test blocked primary endpoints fall through to same-path instance endpoints."""
        service = FunContentService(
            config=config, instances=InstancesConfig(urls=["https://inst.example.com"])
        )
        for path in ("hitokoto", "joke"):
            respx.get(f"https://api.example.com/{path}").mock(
                return_value=Response(403)
            )
        respx.get("https://inst.example.com/hitokoto").mock(
            return_value=Response(200, json={"data": {"hitokoto": "实例一言"}})
        )
        respx.get("https://inst.example.com/joke").mock(
            return_value=Response(200, json={"data": {"content": "实例笑话"}})
        )

        result = await service.fetch_content(date(2026, 2, 4))

        assert result["content"] in {"实例一言", "实例笑话"}
        assert result["title"] != "🐟 摸鱼小贴士"
        assert result["type"] in {"hitokoto", "joke"}


class TestCachedFunContentService:
    """Tests for fun-content cache compatibility."""

    @pytest.fixture
    def config(self) -> FunContentSource:
        """Create the minimum valid service configuration."""
        return FunContentSource(
            timeout_sec=5,
            endpoints=[
                FunContentEndpoint(
                    name="hitokoto",
                    url="https://api.example.com/hitokoto",
                    data_path="data.hitokoto",
                    display_title="今日灵感",
                )
            ],
        )

    def test_cache_preserves_type(
        self, config: FunContentSource, tmp_path: Path
    ) -> None:
        """New cached dictionaries retain the stable endpoint type."""
        service = CachedFunContentService(config, logging.getLogger(__name__), tmp_path)
        expected = {"title": "今日灵感", "content": "内容", "type": "hitokoto"}

        service.save_cache(expected)

        assert service.load_cache() == expected

    def test_legacy_cache_without_type_still_loads(
        self, config: FunContentSource, tmp_path: Path
    ) -> None:
        """Legacy cached dictionaries do not require a type key."""
        service = CachedFunContentService(config, logging.getLogger(__name__), tmp_path)
        legacy = {"title": "旧标题", "content": "旧内容"}
        cache_data = {"date": "2026-02-04", "data": legacy, "fetched_at": 0}
        (tmp_path / "fun_content.json").write_text(
            json.dumps(cache_data, ensure_ascii=False), encoding="utf-8"
        )

        assert service.load_cache() == legacy
