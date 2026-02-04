"""Tests for app/services/kfc.py - KFC Crazy Thursday service."""

import httpx
import pytest
import respx
from httpx import Response

from app.core.config import CrazyThursdayConfig
from app.services.kfc import KfcService


class TestKfcService:
    """Tests for KfcService class."""

    @pytest.fixture
    def enabled_config(self) -> CrazyThursdayConfig:
        """Create an enabled KFC configuration."""
        return CrazyThursdayConfig(
            enabled=True,
            url="https://api.example.com/kfc",
            timeout_sec=5
        )

    @pytest.fixture
    def disabled_config(self) -> CrazyThursdayConfig:
        """Create a disabled KFC configuration."""
        return CrazyThursdayConfig(
            enabled=False,
            url="https://api.example.com/kfc",
            timeout_sec=5
        )

    @pytest.fixture
    def service(self, enabled_config: CrazyThursdayConfig) -> KfcService:
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
        self, disabled_config: CrazyThursdayConfig
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
