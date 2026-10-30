"""Shared image URLs and conditional HTTP cache validation."""

import hashlib
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

from fastapi import Request


def build_image_url(base_domain: str, filename: str) -> str:
    """Build full image URL from base domain and filename."""
    return f"{base_domain.rstrip('/')}/static/{filename}"


def cache_etag(updated_at: int, identity: str | None = None) -> str:
    """Build a stable ETag, optionally scoped to a selected image variant."""
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
    """Check if client cache is still valid (304 Not Modified).

    Args:
        request: FastAPI request object
        updated_at: Update timestamp in milliseconds

    Returns:
        True if client cache is valid (should return 304)
    """
    etag = cache_etag(updated_at, identity)

    # Check If-None-Match header
    if_none_match = request.headers.get("If-None-Match")
    if if_none_match is not None:
        validators = [validator.strip() for validator in if_none_match.split(",")]
        return any(
            validator == "*" or validator.removeprefix("W/") == etag
            for validator in validators
        )

    # Check If-Modified-Since header
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
