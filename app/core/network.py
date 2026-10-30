"""出站 HTTP 与浏览器代理配置的网络工具。"""

import re
from typing import Any
from urllib.parse import unquote, urlsplit, urlunsplit

import httpx

_ALLOWED_PROXY_SCHEMES = {"http", "socks5"}
_URL_IN_TEXT_RE = re.compile(r"(?:https?|socks5)://\S+")
_MAX_LOG_MESSAGE_LEN = 800


def _format_host_port(hostname: str, port: int | None) -> str:
    """拼接主机名与可选端口，保留 IPv6 方括号语法。"""
    host = (
        f"[{hostname}]"
        if ":" in hostname and not hostname.startswith("[")
        else hostname
    )
    return f"{host}:{port}" if port is not None else host


def normalize_proxy_url(value: str | None) -> str | None:
    """归一化并校验可选的代理 URL。

    支持 HTTP 与 SOCKS5 代理，允许通过 userinfo 提供认证凭据；
    不接受 path/query/fragment，限制配置范围，便于审计。
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
    """返回可安全写入日志的 URL：移除 userinfo 与查询数据。"""
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
    """从日志文本中移除 URL 凭据、查询串与 fragment。"""
    return _URL_IN_TEXT_RE.sub(lambda match: redact_url(match.group(0)), value)


def safe_exception_for_log(
    exc: BaseException, *known_urls: str | None,
    sensitive_values: tuple[str, ...] = (),
) -> str:
    """返回可安全写入日志的异常诊断摘要。"""
    exc_type = type(exc).__name__
    parts: list[str] = []

    if isinstance(exc, httpx.HTTPStatusError):
        parts.append(f"status={exc.response.status_code}")

    request = getattr(exc, "request", None)
    request_url = getattr(request, "url", None)
    if request_url is not None:
        parts.append("url=<redacted-url>" if sensitive_values else f"url={redact_url(str(request_url))}")

    message = str(exc).strip()
    # Webhook 路径可能含 bot token，先移除完整 URL，避免凭据泄露。
    if sensitive_values:
        message = _URL_IN_TEXT_RE.sub("<redacted-url>", message)
        for value in sorted(filter(None, sensitive_values), key=len, reverse=True):
            message = message.replace(value, "<redacted>")
    message = _sanitize_message(message)
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
    """构造出站 HTTPX 客户端的关键字参数。"""
    client_kwargs = dict(kwargs)
    normalized_proxy_url = normalize_proxy_url(proxy_url)
    if normalized_proxy_url:
        client_kwargs["proxy"] = normalized_proxy_url
    return client_kwargs


def create_async_client(
    proxy_url: str | None = None,
    **kwargs: Any,
) -> httpx.AsyncClient:
    """按项目统一的代理策略创建 HTTPX AsyncClient。"""
    return httpx.AsyncClient(**httpx_client_kwargs(proxy_url=proxy_url, **kwargs))


def playwright_proxy_config(proxy_url: str | None) -> dict[str, str] | None:
    """把配置的代理 URL 转换为 Playwright 的 proxy 字典。"""
    normalized_proxy_url = normalize_proxy_url(proxy_url)
    if not normalized_proxy_url:
        return None

    parsed = urlsplit(normalized_proxy_url)
    if parsed.scheme == "socks5" and (
        parsed.username is not None or parsed.password is not None
    ):
        raise ValueError(
            "Chromium cannot authenticate to SOCKS proxies. "
            "Use an unauthenticated SOCKS proxy, an HTTP proxy with credentials, "
            "or a local forwarder (e.g. gost/privoxy) that adds authentication."
        )
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
