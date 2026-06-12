"""Network helpers for outbound HTTP and browser proxy configuration."""

import re
from typing import Any
from urllib.parse import unquote, urlsplit, urlunsplit

import httpx

_ALLOWED_PROXY_SCHEMES = {"http", "socks5"}
_URL_IN_TEXT_RE = re.compile(r"(?:https?|socks5)://\S+")
_MAX_LOG_MESSAGE_LEN = 800


def _format_host_port(hostname: str, port: int | None) -> str:
    """Format hostname and optional port, preserving IPv6 bracket syntax."""
    host = (
        f"[{hostname}]"
        if ":" in hostname and not hostname.startswith("[")
        else hostname
    )
    return f"{host}:{port}" if port is not None else host


def normalize_proxy_url(value: str | None) -> str | None:
    """Normalize and validate an optional proxy URL.

    Supports HTTP and SOCKS5 proxy URLs. Userinfo is allowed for proxy
    authentication, but path/query/fragment components are intentionally
    rejected to keep the configuration contract narrow and auditable.
    """
    if value is None:
        return None

    proxy_url = value.strip()
    if not proxy_url:
        return None

    parsed = urlsplit(proxy_url)
    scheme = parsed.scheme.lower()
    if scheme not in _ALLOWED_PROXY_SCHEMES:
        allowed = ", ".join(f"{item}://" for item in sorted(_ALLOWED_PROXY_SCHEMES))
        raise ValueError(f"proxy_url must start with one of: {allowed}")
    if not parsed.hostname:
        raise ValueError("proxy_url must include a host")
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise ValueError("proxy_url must not include path, query, or fragment")

    try:
        parsed.port
    except ValueError as exc:
        raise ValueError("proxy_url port is invalid") from exc

    return urlunsplit((scheme, parsed.netloc, "", "", ""))


def redact_url(value: str | None) -> str:
    """Return a log-safe URL string with userinfo and query data removed."""
    if not value:
        return ""
    try:
        parsed = urlsplit(value)
        if not parsed.scheme or not parsed.hostname:
            return "<redacted-url>"
        host_port = _format_host_port(parsed.hostname, parsed.port)
        path = parsed.path if parsed.path and parsed.path != "/" else ""
        return urlunsplit((parsed.scheme, host_port, path, "", ""))
    except Exception:
        return "<redacted-url>"


def _sanitize_message(value: str) -> str:
    """Remove URL credentials, query strings, and fragments from log text."""
    return _URL_IN_TEXT_RE.sub(lambda match: redact_url(match.group(0)), value)


def safe_exception_for_log(exc: BaseException, *known_urls: str | None) -> str:
    """Return a diagnostic exception summary safe for logs."""
    exc_type = type(exc).__name__
    parts: list[str] = []

    if isinstance(exc, httpx.HTTPStatusError):
        parts.append(f"status={exc.response.status_code}")

    request = getattr(exc, "request", None)
    request_url = getattr(request, "url", None)
    if request_url is not None:
        parts.append(f"url={redact_url(str(request_url))}")

    message = _sanitize_message(str(exc).strip())
    for url in known_urls:
        if url:
            message = message.replace(url, redact_url(url))

    if message:
        if len(message) > _MAX_LOG_MESSAGE_LEN:
            message = f"{message[:_MAX_LOG_MESSAGE_LEN]}..."
        parts.append(message)

    if not parts:
        return exc_type
    summary = "; ".join(dict.fromkeys(parts))
    return f"{exc_type}: {summary}"


def httpx_client_kwargs(
    proxy_url: str | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Build keyword arguments for an outbound HTTPX client."""
    client_kwargs = dict(kwargs)
    normalized_proxy_url = normalize_proxy_url(proxy_url)
    if normalized_proxy_url:
        client_kwargs["proxy"] = normalized_proxy_url
    return client_kwargs


def create_async_client(
    proxy_url: str | None = None,
    **kwargs: Any,
) -> httpx.AsyncClient:
    """Create an HTTPX AsyncClient using the repository proxy policy."""
    return httpx.AsyncClient(**httpx_client_kwargs(proxy_url=proxy_url, **kwargs))


def playwright_proxy_config(proxy_url: str | None) -> dict[str, str] | None:
    """Convert the configured proxy URL into Playwright's proxy dictionary."""
    normalized_proxy_url = normalize_proxy_url(proxy_url)
    if not normalized_proxy_url:
        return None

    parsed = urlsplit(normalized_proxy_url)
    if not parsed.hostname:
        return None

    proxy: dict[str, str] = {
        "server": f"{parsed.scheme}://{_format_host_port(parsed.hostname, parsed.port)}"
    }
    if parsed.username:
        proxy["username"] = unquote(parsed.username)
    if parsed.password:
        proxy["password"] = unquote(parsed.password)
    return proxy
