"""Operations API endpoints (API Key protected)."""

import asyncio
import hmac
import json
import logging
from pathlib import Path

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from app.core.errors import ErrorCode, error_response, get_http_status
from app.services.calendar import today_business
from app.services.generator import GenerationBusyError, generate_and_save_image

router = APIRouter(prefix="/api/v1/ops", tags=["ops"])


class _UnauthorizedError(Exception):
    """Internal exception for auth failures."""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


def _verify_api_key(request: Request) -> None:
    """Verify API Key from Authorization: Bearer <key> header."""
    config = request.app.state.config
    expected_key = config.ops.api_key

    if not expected_key:
        raise _UnauthorizedError("无效的 API Key")

    auth_header = request.headers.get("authorization", "")
    if not auth_header.startswith("Bearer "):
        raise _UnauthorizedError("无效的 API Key")

    token = auth_header[7:]
    if not hmac.compare_digest(token, expected_key):
        raise _UnauthorizedError("无效的 API Key")


def _auth_error_response(message: str) -> JSONResponse:
    """Create 401 Unauthorized response."""
    return JSONResponse(
        content=error_response(ErrorCode.AUTH_UNAUTHORIZED, message),
        status_code=get_http_status(ErrorCode.AUTH_UNAUTHORIZED),
        headers={"Cache-Control": "no-store", "WWW-Authenticate": "Bearer"},
    )


@router.get("/generate")
async def ops_generate(request: Request) -> JSONResponse:
    """手动触发图片生成（同步阻塞，需鉴权）。"""
    logger = logging.getLogger("moyuren")

    try:
        _verify_api_key(request)
    except _UnauthorizedError as e:
        return _auth_error_response(e.message)

    try:
        filename = await generate_and_save_image(request.app)
        data_dir = Path(request.app.state.config.paths.cache_dir) / "data"
        today_str = today_business().isoformat()
        data_file = data_dir / f"{today_str}.json"
        images: dict = {}
        if data_file.exists():
            try:
                with data_file.open("r", encoding="utf-8") as f:
                    data = json.load(f)
                images = data.get("images", {})
            except (OSError, json.JSONDecodeError):
                pass

        return JSONResponse(
            content={"data": {"date": today_str, "filename": filename, "images": images}},
            headers={"Cache-Control": "no-store"},
        )
    except GenerationBusyError:
        return JSONResponse(
            content=error_response(ErrorCode.GENERATION_BUSY, "图片正在生成中，请稍后重试"),
            status_code=get_http_status(ErrorCode.GENERATION_BUSY),
            headers={"Retry-After": "10", "Cache-Control": "no-store"},
        )
    except Exception as e:
        logger.error(f"Manual generation failed: {e}")
        return JSONResponse(
            content=error_response(ErrorCode.GENERATION_FAILED, "图片生成失败"),
            status_code=get_http_status(ErrorCode.GENERATION_FAILED),
            headers={"Cache-Control": "no-store"},
        )


@router.get("/cache/clean")
async def ops_cache_clean(
    request: Request,
    keep_days: int | None = Query(None, description="保留最近 N 天的数据"),
) -> JSONResponse:
    """清理过期缓存文件（需鉴权）。"""
    logger = logging.getLogger("moyuren")

    try:
        _verify_api_key(request)
    except _UnauthorizedError as e:
        return _auth_error_response(e.message)

    if keep_days is not None and keep_days <= 0:
        return JSONResponse(
            content=error_response(ErrorCode.API_INVALID_PARAMETER, "keep_days 必须为正整数"),
            status_code=get_http_status(ErrorCode.API_INVALID_PARAMETER),
            headers={"Cache-Control": "no-store"},
        )

    try:
        cache_cleaner = request.app.state.services.cache_cleaner
        if keep_days is not None:
            result = await asyncio.to_thread(cache_cleaner.cleanup, retain_days=keep_days)
        else:
            result = await asyncio.to_thread(cache_cleaner.cleanup)

        return JSONResponse(content={"data": result}, headers={"Cache-Control": "no-store"})
    except Exception as e:
        logger.error(f"Cache cleanup failed: {e}")
        return JSONResponse(
            content=error_response(ErrorCode.OPS_CACHE_CLEAN_FAILED, "缓存清理失败"),
            status_code=get_http_status(ErrorCode.OPS_CACHE_CLEAN_FAILED),
            headers={"Cache-Control": "no-store"},
        )
