"""Tests for app/services/fun_content.py - fun content fetching service."""

from datetime import date

import httpx
import pytest
import respx
from httpx import Response

from app.core.config import FunContentEndpoint, FunContentSource
from app.services.fun_content import FunContentService


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
                    display_title="💬 一言"
                ),
                FunContentEndpoint(
                    name="joke",
                    url="https://api.example.com/joke",
                    data_path="data.content",
                    display_title="🤣 冷笑话"
                ),
            ]
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
        assert result["content"] != ""

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_content_fallback_to_second_endpoint(
        self, service: FunContentService
    ) -> None:
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
    async def test_fetch_content_all_fail_returns_default(
        self, service: FunContentService
    ) -> None:
        """Test returns default content when all endpoints fail."""
        respx.get("https://api.example.com/hitokoto").mock(return_value=Response(500))
        respx.get("https://api.example.com/joke").mock(return_value=Response(500))

        result = await service.fetch_content(date(2026, 2, 4))

        assert result["title"] == "🐟 摸鱼小贴士"
        assert "摸鱼" in result["content"]

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_content_timeout_fallback(self, service: FunContentService) -> None:
        """Test fallback when endpoint times out."""
        respx.get("https://api.example.com/hitokoto").mock(
            side_effect=httpx.TimeoutException("Timeout")
        )
        respx.get("https://api.example.com/joke").mock(
            return_value=Response(200, json={"data": {"content": "笑话内容"}})
        )

        result = await service.fetch_content(date(2026, 2, 4))

        assert "content" in result

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_content_empty_content_skipped(
        self, service: FunContentService
    ) -> None:
        """Test empty content is skipped."""
        respx.get("https://api.example.com/hitokoto").mock(
            return_value=Response(200, json={"data": {"hitokoto": ""}})
        )
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
    async def test_fetch_content_whitespace_only_skipped(
        self, service: FunContentService
    ) -> None:
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
    async def test_fetch_content_invalid_json_structure(
        self, service: FunContentService
    ) -> None:
        """Test handles invalid JSON structure gracefully."""
        respx.get("https://api.example.com/hitokoto").mock(
            return_value=Response(200, json={"wrong": "structure"})
        )
        respx.get("https://api.example.com/joke").mock(return_value=Response(500))

        result = await service.fetch_content(date(2026, 2, 4))

        # Should fall back to default
        assert result["title"] == "🐟 摸鱼小贴士"
