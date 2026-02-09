"""Tests for app/services/kfc.py - KFC Crazy Thursday service."""

import logging
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx
from httpx import Response

from app.core.config import CrazyThursdaySource
from app.services.kfc import CachedKfcService, KfcService


class TestKfcService:
    """Tests for KfcService class."""

    @pytest.fixture
    def enabled_config(self) -> CrazyThursdaySource:
        """Create an enabled KFC configuration."""
        return CrazyThursdaySource(
            enabled=True,
            url="https://api.example.com/kfc",
            timeout_sec=5
        )

    @pytest.fixture
    def disabled_config(self) -> CrazyThursdaySource:
        """Create a disabled KFC configuration."""
        return CrazyThursdaySource(
            enabled=False,
            url="https://api.example.com/kfc",
            timeout_sec=5
        )

    @pytest.fixture
    def service(self, enabled_config: CrazyThursdaySource) -> KfcService:
        """Create a KfcService instance with enabled config."""
        return KfcService(config=enabled_config)

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_kfc_copy_success_viki_format(self, service: KfcService) -> None:
        """Test successful fetch with Viki API format."""
        respx.get("https://api.example.com/kfc").mock(
            return_value=Response(200, json={"code": 200, "data": {"kfc": "V我50"}})
        )

        result = await service.fetch_kfc_copy()

        assert result == "V我50"

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_kfc_copy_success_string_data(self, service: KfcService) -> None:
        """Test successful fetch with string data format."""
        respx.get("https://api.example.com/kfc").mock(
            return_value=Response(200, json={"code": 200, "data": "V我50"})
        )

        result = await service.fetch_kfc_copy()

        assert result == "V我50"

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_kfc_copy_success_text_field(self, service: KfcService) -> None:
        """Test successful fetch with text field format."""
        respx.get("https://api.example.com/kfc").mock(
            return_value=Response(200, json={"text": "V我50"})
        )

        result = await service.fetch_kfc_copy()

        assert result == "V我50"

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_kfc_copy_handles_escaped_newlines(self, service: KfcService) -> None:
        """Test handles escaped newlines in content."""
        respx.get("https://api.example.com/kfc").mock(
            return_value=Response(200, json={"code": 200, "data": {"kfc": "Line1\\nLine2"}})
        )

        result = await service.fetch_kfc_copy()

        assert result == "Line1\nLine2"

    @pytest.mark.asyncio
    async def test_fetch_kfc_copy_disabled_returns_none(
        self, disabled_config: CrazyThursdaySource
    ) -> None:
        """Test disabled config returns None without making request."""
        service = KfcService(config=disabled_config)

        result = await service.fetch_kfc_copy()

        assert result is None

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_kfc_copy_timeout(self, service: KfcService) -> None:
        """Test timeout returns None."""
        respx.get("https://api.example.com/kfc").mock(
            side_effect=httpx.TimeoutException("Timeout")
        )

        result = await service.fetch_kfc_copy()

        assert result is None

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_kfc_copy_http_error(self, service: KfcService) -> None:
        """Test HTTP error returns None."""
        respx.get("https://api.example.com/kfc").mock(return_value=Response(500))

        result = await service.fetch_kfc_copy()

        assert result is None

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_kfc_copy_empty_content(self, service: KfcService) -> None:
        """Test empty content returns None."""
        respx.get("https://api.example.com/kfc").mock(
            return_value=Response(200, json={"code": 200, "data": {"kfc": ""}})
        )

        result = await service.fetch_kfc_copy()

        assert result is None

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_kfc_copy_null_content(self, service: KfcService) -> None:
        """Test null content returns None."""
        respx.get("https://api.example.com/kfc").mock(
            return_value=Response(200, json={"code": 200, "data": {"kfc": None}})
        )

        result = await service.fetch_kfc_copy()

        assert result is None

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_kfc_copy_invalid_json(self, service: KfcService) -> None:
        """Test invalid JSON returns None."""
        respx.get("https://api.example.com/kfc").mock(
            return_value=Response(200, content=b"not json")
        )

        result = await service.fetch_kfc_copy()

        assert result is None

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_kfc_copy_strips_whitespace(self, service: KfcService) -> None:
        """Test content whitespace is stripped."""
        respx.get("https://api.example.com/kfc").mock(
            return_value=Response(200, json={"code": 200, "data": {"kfc": "  V我50  "}})
        )

        result = await service.fetch_kfc_copy()

        assert result == "V我50"

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_kfc_copy_string_response(self, service: KfcService) -> None:
        """Test handles string response format."""
        respx.get("https://api.example.com/kfc").mock(
            return_value=Response(200, json="V我50")
        )

        result = await service.fetch_kfc_copy()

        assert result == "V我50"


class TestCachedKfcService:
    """Tests for CachedKfcService class."""

    @pytest.fixture
    def config(self) -> CrazyThursdaySource:
        """Create an enabled KFC configuration."""
        return CrazyThursdaySource(
            enabled=True,
            url="https://api.example.com/kfc",
            timeout_sec=5
        )

    @pytest.fixture
    def cache_dir(self, tmp_path: Path) -> Path:
        """Create a temporary cache directory."""
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        return cache_dir

    @pytest.fixture
    def logger_instance(self) -> logging.Logger:
        """Create a logger instance."""
        return logging.getLogger("test_kfc")

    @pytest.fixture
    def service(
        self, config: CrazyThursdaySource, logger_instance: logging.Logger, cache_dir: Path
    ) -> CachedKfcService:
        """Create a CachedKfcService instance."""
        return CachedKfcService(config=config, logger=logger_instance, cache_dir=cache_dir)

    @pytest.mark.asyncio
    async def test_get_returns_none_on_non_thursday(
        self, service: CachedKfcService
    ) -> None:
        """Test get() returns None when not Thursday."""
        # Mock today_business to return a non-Thursday (Monday = 0)
        with patch("app.services.kfc.today_business") as mock_today:
            mock_today.return_value = date(2026, 2, 2)  # Monday
            result = await service.get()
            assert result is None

    @pytest.mark.asyncio
    async def test_get_fetches_on_thursday(self, service: CachedKfcService) -> None:
        """Test get() fetches content on Thursday."""
        with patch("app.services.kfc.today_business") as mock_today:
            mock_today.return_value = date(2026, 2, 5)  # Thursday
            with patch.object(
                service._service, "fetch_kfc_copy", new_callable=AsyncMock
            ) as mock_fetch:
                mock_fetch.return_value = "V我50"
                result = await service.get()
                assert result == "V我50"
                mock_fetch.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_uses_cache_on_thursday(
        self, config: CrazyThursdaySource, logger_instance: logging.Logger, cache_dir: Path
    ) -> None:
        """Test get() uses cache on subsequent calls on Thursday."""
        service = CachedKfcService(config=config, logger=logger_instance, cache_dir=cache_dir)

        with patch("app.services.kfc.today_business") as mock_today:
            mock_today.return_value = date(2026, 2, 5)  # Thursday
            with patch.object(
                service._service, "fetch_kfc_copy", new_callable=AsyncMock
            ) as mock_fetch:
                mock_fetch.return_value = "V我50"

                # First call - should fetch
                result1 = await service.get()
                assert result1 == "V我50"
                assert mock_fetch.call_count == 1

                # Second call - should use cache
                result2 = await service.get()
                assert result2 == "V我50"
                assert mock_fetch.call_count == 1  # Still 1, used cache

    @pytest.mark.asyncio
    async def test_get_force_refresh_on_thursday(
        self, config: CrazyThursdaySource, logger_instance: logging.Logger, cache_dir: Path
    ) -> None:
        """Test get() with force_refresh bypasses cache on Thursday."""
        service = CachedKfcService(config=config, logger=logger_instance, cache_dir=cache_dir)

        with patch("app.services.kfc.today_business") as mock_today:
            mock_today.return_value = date(2026, 2, 5)  # Thursday
            with patch.object(
                service._service, "fetch_kfc_copy", new_callable=AsyncMock
            ) as mock_fetch:
                mock_fetch.return_value = "V我50"

                # First call
                await service.get()
                assert mock_fetch.call_count == 1

                # Force refresh
                mock_fetch.return_value = "新文案"
                result = await service.get(force_refresh=True)
                assert result == "新文案"
                assert mock_fetch.call_count == 2

    @pytest.mark.asyncio
    async def test_fetch_fresh_returns_none_on_non_thursday(
        self, service: CachedKfcService
    ) -> None:
        """Test fetch_fresh() returns None when not Thursday."""
        with patch("app.services.kfc.today_business") as mock_today:
            mock_today.return_value = date(2026, 2, 2)  # Monday
            result = await service.fetch_fresh()
            assert result is None

    @pytest.mark.asyncio
    async def test_fetch_fresh_fetches_on_thursday(
        self, service: CachedKfcService
    ) -> None:
        """Test fetch_fresh() fetches content on Thursday."""
        with patch("app.services.kfc.today_business") as mock_today:
            mock_today.return_value = date(2026, 2, 5)  # Thursday
            with patch.object(
                service._service, "fetch_kfc_copy", new_callable=AsyncMock
            ) as mock_fetch:
                mock_fetch.return_value = "V我50"
                result = await service.fetch_fresh()
                assert result == "V我50"

    @pytest.mark.asyncio
    async def test_fetch_fresh_handles_exception(
        self, service: CachedKfcService
    ) -> None:
        """Test fetch_fresh() handles exceptions gracefully."""
        with patch("app.services.kfc.today_business") as mock_today:
            mock_today.return_value = date(2026, 2, 5)  # Thursday
            with patch.object(
                service._service, "fetch_kfc_copy", new_callable=AsyncMock
            ) as mock_fetch:
                mock_fetch.side_effect = Exception("Network error")
                result = await service.fetch_fresh()
                assert result is None
