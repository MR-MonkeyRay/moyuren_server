"""Operations API endpoints (API Key protected)."""

import asyncio
import hmac
import logging
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from starlette.datastructures import MutableHeaders
from starlette.types import Message, Receive, Scope, Send

from app.core.errors import ErrorCode, StorageError, error_response, get_http_status
from app.core.services import AppServices
from app.models.schemas import DailyDataFile
from app.services.calendar import today_business
from app.services.generator import GenerationBusyError, generate_and_save_image

GET_DEPRECATION_TIMESTAMP = int(datetime(2026, 10, 30, tzinfo=UTC).timestamp())


class _OpsRoute(APIRoute):
    """Attach deprecation headers to every legacy GET handler response."""

    async def handle(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Wrap ops responses, including auth, validation and operation errors."""
        if scope["method"] != "GET":
            await super().handle(scope, receive, send)
            return

        logging.getLogger("moyuren").warning(
            "GET %s is deprecated; use POST at the same URL", scope["path"]
        )

        async def send_with_deprecation(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["Deprecation"] = f"@{GET_DEPRECATION_TIMESTAMP}"
            await send(message)

        await super().handle(scope, receive, send_with_deprecation)


router = APIRouter(prefix="/api/v1/ops", tags=["ops"], route_class=_OpsRoute)


class _UnauthorizedError(Exception):
    """Internal exception for auth failures."""

    def __init__(self, message: str) -> None:
        """初始化鉴权失败异常.

        Args:
            message: 返回给调用方的鉴权失败说明.
        """
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
    if not hmac.compare_digest(token.encode("utf-8"), expected_key.encode("utf-8")):
        raise _UnauthorizedError("无效的 API Key")


def _auth_error_response(message: str) -> JSONResponse:
    """Create 401 Unauthorized response."""
    return JSONResponse(
        content=error_response(ErrorCode.AUTH_UNAUTHORIZED, message),
        status_code=get_http_status(ErrorCode.AUTH_UNAUTHORIZED),
        headers={"Cache-Control": "no-store", "WWW-Authenticate": "Bearer"},
    )


@router.get("/generate", deprecated=True)
@router.post("/generate")
async def ops_generate(request: Request) -> JSONResponse:
    """手动触发图片生成（同步阻塞，需鉴权）。"""
    logger = logging.getLogger("moyuren")

    try:
        _verify_api_key(request)
    except _UnauthorizedError as e:
        return _auth_error_response(e.message)

    try:
        results = await generate_and_save_image(request.app, trigger="manual")
        templates_config = request.app.state.config.get_templates_config()
        total_templates = len(templates_config.items)
        data_dir = Path(request.app.state.config.paths.cache_dir) / "data"
        today_str = today_business().isoformat()
        data_file = data_dir / f"{today_str}.json"
        images: dict[str, str] = {}
        if data_file.exists():
            try:
                images = DailyDataFile.load(data_file).images
            except StorageError:
                pass

        return JSONResponse(
            content={
                "data": {
                    "date": today_str,
                    "results": results,
                    "total_templates": total_templates,
                    "images": images,
                }
            },
            headers={"Cache-Control": "no-store"},
        )
    except GenerationBusyError:
        return JSONResponse(
            content=error_response(ErrorCode.GENERATION_BUSY, "图片正在生成中，请稍后重试"),
            status_code=get_http_status(ErrorCode.GENERATION_BUSY),
            headers={"Retry-After": "10", "Cache-Control": "no-store"},
        )
    except Exception as e:
        logger.error("Manual generation failed: %s", e)
        return JSONResponse(
            content=error_response(ErrorCode.GENERATION_FAILED, "图片生成失败"),
            status_code=get_http_status(ErrorCode.GENERATION_FAILED),
            headers={"Cache-Control": "no-store"},
        )


@router.get("/cache/clean", deprecated=True)
@router.post("/cache/clean")
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
        services: AppServices = request.app.state.services
        cache_cleaner = services.cache_cleaner
        if keep_days is not None:
            result = await asyncio.to_thread(cache_cleaner.cleanup, retain_days=keep_days)
        else:
            result = await asyncio.to_thread(cache_cleaner.cleanup)

        return JSONResponse(content={"data": result}, headers={"Cache-Control": "no-store"})
    except Exception as e:
        logger.error("Cache cleanup failed: %s", e)
        return JSONResponse(
            content=error_response(ErrorCode.OPS_CACHE_CLEAN_FAILED, "缓存清理失败"),
            status_code=get_http_status(ErrorCode.OPS_CACHE_CLEAN_FAILED),
            headers={"Cache-Control": "no-store"},
        )
