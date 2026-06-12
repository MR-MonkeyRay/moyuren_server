"""Tests for app/core/network.py."""

import httpx
import pytest

from app.core.network import (
    httpx_client_kwargs,
    normalize_proxy_url,
    playwright_proxy_config,
    redact_url,
    safe_exception_for_log,
)


class TestProxyUrl:
    """Tests for proxy URL normalization and redaction."""

    def test_normalize_empty_proxy(self) -> None:
        """Test empty proxy config disables proxy."""
        assert normalize_proxy_url(None) is None
        assert normalize_proxy_url("") is None
        assert normalize_proxy_url("   ") is None

    @pytest.mark.parametrize(
        "value",
        [
            "http://proxy.example:8080",
            "socks5://user:pass@proxy.example:1080",
        ],
    )
    def test_normalize_supported_proxy(self, value: str) -> None:
        """Test supported proxy URLs pass through normalized."""
        assert normalize_proxy_url(value) == value

    @pytest.mark.parametrize(
        "value",
        [
            "ftp://proxy.example",
            "https://proxy.example:8443",
            "http://",
            "http://proxy.example/path",
            "http://proxy.example?token=value",
            "http://proxy.example#frag",
        ],
    )
    def test_normalize_rejects_invalid_proxy(self, value: str) -> None:
        """Test unsupported proxy URLs fail closed."""
        with pytest.raises(ValueError):
            normalize_proxy_url(value)

    def test_redact_url_removes_userinfo_and_query(self) -> None:
        """Test redaction strips credentials and query fragments."""
        assert (
            redact_url("http://user:secret@proxy.example:8080/path?q=1#frag")
            == "http://proxy.example:8080/path"
        )

    def test_httpx_client_kwargs_sets_proxy_without_declaring_trust_env(self) -> None:
        """Test proxy kwargs do not override HTTPX environment defaults."""
        kwargs = httpx_client_kwargs(proxy_url="http://proxy.example:8080", timeout=5)
        assert kwargs["proxy"] == "http://proxy.example:8080"
        assert kwargs["timeout"] == 5
        assert "trust_env" not in kwargs

    def test_safe_exception_for_log_preserves_status_and_redacts_url_secrets(
        self,
    ) -> None:
        """Test HTTP errors keep useful diagnostics without leaking URL secrets."""
        request = httpx.Request(
            "GET", "https://user:secret@example.com/path?token=abc#frag"
        )
        response = httpx.Response(502, request=request)
        exc = httpx.HTTPStatusError(
            "upstream failed for https://user:secret@example.com/path?token=abc#frag",
            request=request,
            response=response,
        )

        message = safe_exception_for_log(exc)

        assert "HTTPStatusError" in message
        assert "status=502" in message
        assert "https://example.com/path" in message
        assert "secret" not in message
        assert "token" not in message
        assert "frag" not in message

    def test_safe_exception_for_log_redacts_urls_embedded_in_message(self) -> None:
        """Test arbitrary exception messages have embedded URL secrets removed."""
        exc = RuntimeError(
            "failed https://user:pass@example.com/path?token=secret#frag after retry"
        )

        message = safe_exception_for_log(exc)

        assert message.startswith("RuntimeError:")
        assert "https://example.com/path" in message
        assert "after retry" in message
        assert "user" not in message
        assert "pass" not in message
        assert "token" not in message
        assert "frag" not in message

    def test_playwright_proxy_config_splits_credentials(self) -> None:
        """Test Playwright proxy config receives server and credentials separately."""
        proxy = playwright_proxy_config("socks5://user:pass@proxy.example:1080")
        assert proxy == {
            "server": "socks5://proxy.example:1080",
            "username": "user",
            "password": "pass",
        }
