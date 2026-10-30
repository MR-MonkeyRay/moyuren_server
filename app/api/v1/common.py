"""图片 URL 拼接与 HTTP 条件请求缓存校验。"""

import hashlib
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

from fastapi import Request


def build_image_url(base_domain: str, filename: str) -> str:
    """根据公开域名与文件名拼出图片完整 URL。"""
    return f"{base_domain.rstrip('/')}/static/{filename}"


def cache_etag(updated_at: int, identity: str | None = None) -> str:
    """生成稳定的 ETag；传入 identity 时限定到具体图片变体。"""
    if identity is None:
        return f'"{updated_at}"'
    digest = hashlib.sha256(identity.encode()).hexdigest()[:16]
    return f'"{updated_at}-{digest}"'


def check_not_modified(
    request: Request,
    updated_at: int,
    identity: str | None = None,
    *,
    allow_if_modified_since: bool = True,
) -> bool:
    """判断客户端缓存是否仍有效（可返回 304 Not Modified）。

    Args:
        request: FastAPI 请求对象
        updated_at: 毫秒级更新时间戳

    Returns:
        True 表示客户端缓存有效，应返回 304
    """
    etag = cache_etag(updated_at, identity)

    # 校验 If-None-Match 请求头
    if_none_match = request.headers.get("If-None-Match")
    if if_none_match is not None:
        validators = [validator.strip() for validator in if_none_match.split(",")]
        return any(
            validator == "*" or validator.removeprefix("W/") == etag
            for validator in validators
        )

    # 校验 If-Modified-Since 请求头
    if_modified_since = (
        request.headers.get("If-Modified-Since") if allow_if_modified_since else None
    )
    if if_modified_since:
        try:
            client_time = parsedate_to_datetime(if_modified_since)
            if client_time.tzinfo is None:
                client_time = client_time.replace(tzinfo=UTC)
            client_time = client_time.astimezone(UTC)
            server_time = datetime.fromtimestamp(updated_at // 1000, tz=UTC)
            if client_time >= server_time:
                return True
        except (ValueError, TypeError, OverflowError):
            pass

    return False
