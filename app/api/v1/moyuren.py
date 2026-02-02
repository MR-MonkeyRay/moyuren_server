"""Moyuren image API endpoints."""

import asyncio
import json
import logging
from pathlib import Path

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse, FileResponse

from app.core.errors import ErrorCode, error_response
from app.models.schemas import (
    ErrorResponse,
    MoyurenImageResponse,
    MoyurenDetailResponse,
)
from app.services.generator import GenerationBusyError, generate_and_save_image

router = APIRouter(prefix="/api/v1", tags=["moyuren"])


async def _ensure_state_file_exists(request: Request, state_path: Path, logger) -> JSONResponse | None:
    """Ensure state file exists, trigger generation if needed.

    Returns:
        None if state file exists, JSONResponse with error if generation fails.
    """
    if not state_path.exists():
        logger.info("State file not found, triggering image generation...")
        try:
            await generate_and_save_image(request.app)
        except GenerationBusyError:
            # 另一个进程正在生成，循环等待直到生成完成
            logger.info("Generation in progress by another process, waiting...")
            max_wait_seconds = 60

            for wait_count in range(1, max_wait_seconds + 1):
                # 先检查再等待，避免额外延迟
                if state_path.exists():
                    logger.info(f"Generation completed after {wait_count - 1} seconds")
                    break
                await asyncio.sleep(1)
                # 每 10 秒记录一次进度，减少日志噪音
                if wait_count % 10 == 0:
                    logger.info(f"Still waiting for generation... ({wait_count}/{max_wait_seconds}s)")
            else:
                # 超时后返回 503
                logger.warning(f"Generation timeout after {max_wait_seconds} seconds")
                return JSONResponse(
                    content=error_response(
                        code=ErrorCode.GENERATION_BUSY,
                        message="Image generation timeout",
                        detail=f"Generation did not complete within {max_wait_seconds} seconds",
                    ),
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                )
        except Exception as e:
            logger.error(f"On-demand image generation failed: {e}")
            return JSONResponse(
                content=error_response(
                    code=ErrorCode.GENERATION_FAILED,
                    message="Image generation failed",
                    detail=str(e),
                ),
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
    return None


async def _read_state_file(state_path: Path, logger) -> tuple[dict | None, JSONResponse | None]:
    """Read and validate state file.

    Returns:
        Tuple of (state_data, error_response). If successful, error_response is None.
    """
    try:
        with state_path.open("r", encoding="utf-8") as f:
            state_data = json.load(f)
    except FileNotFoundError:
        logger.error("State file disappeared after generation")
        return None, JSONResponse(
            content=error_response(
                code=ErrorCode.STORAGE_NOT_FOUND,
                message="No image available",
                detail="State file not found after generation attempt",
            ),
            status_code=status.HTTP_404_NOT_FOUND,
        )
    except OSError as e:
        logger.error(f"Failed to read state file: {e}")
        return None, JSONResponse(
            content=error_response(
                code=ErrorCode.STORAGE_READ_FAILED,
                message="Failed to read state file",
                detail=str(e),
            ),
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse state file: {e}")
        return None, JSONResponse(
            content=error_response(
                code=ErrorCode.STORAGE_READ_FAILED,
                message="Invalid state file",
                detail=str(e),
            ),
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    # Validate state data - must be a dict with required fields
    if not isinstance(state_data, dict):
        logger.warning(f"State file has invalid format: expected dict, got {type(state_data).__name__}")
        return None, JSONResponse(
            content=error_response(
                code=ErrorCode.STORAGE_READ_FAILED,
                message="Invalid state file",
                detail=f"Expected dict, got {type(state_data).__name__}",
            ),
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    required_fields = ["filename", "date", "updated", "updated_at"]
    missing_fields = [f for f in required_fields if f not in state_data]
    if missing_fields:
        logger.warning(f"State file missing required fields: {missing_fields}, will regenerate")
        return None, None  # Signal to regenerate

    # Validate field values
    try:
        # Validate filename and date are non-empty strings
        if not state_data.get("filename") or not state_data.get("date"):
            logger.warning("State file has empty filename or date, will regenerate")
            return None, None

        # Validate updated is RFC3339 format
        from datetime import datetime
        updated_str = state_data.get("updated")
        if not updated_str or not isinstance(updated_str, str):
            logger.warning("State file has invalid updated field, will regenerate")
            return None, None

        # Try parsing RFC3339 format
        try:
            datetime.fromisoformat(updated_str)
        except (ValueError, TypeError):
            logger.warning(f"State file updated field not RFC3339 format: {updated_str}, will regenerate")
            return None, None

        # Validate updated_at is positive integer
        updated_at = state_data.get("updated_at")
        if not isinstance(updated_at, int) or updated_at <= 0:
            logger.warning(f"State file updated_at is not positive integer: {updated_at}, will regenerate")
            return None, None

    except Exception as e:
        logger.warning(f"State file validation error: {e}, will regenerate")
        return None, None

    return state_data, None


async def _get_valid_state(request: Request, state_path: Path, logger) -> tuple[dict | None, JSONResponse | None]:
    """Get valid state data, regenerating if necessary.

    Returns:
        Tuple of (state_data, error_response). If successful, error_response is None.
    """
    # Ensure state file exists
    if error := await _ensure_state_file_exists(request, state_path, logger):
        return None, error

    # Read state file
    state_data, error = await _read_state_file(state_path, logger)
    if error:
        return None, error

    # If state_data is None (missing required fields), regenerate
    if state_data is None:
        logger.info("State file incompatible, triggering regeneration...")
        state_path.unlink(missing_ok=True)
        if error := await _ensure_state_file_exists(request, state_path, logger):
            return None, error
        state_data, error = await _read_state_file(state_path, logger)
        if error:
            return None, error
        if state_data is None:
            return None, JSONResponse(
                content=error_response(
                    code=ErrorCode.GENERATION_FAILED,
                    message="Failed to regenerate image",
                    detail="State file still incompatible after regeneration",
                ),
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    return state_data, None


@router.get(
    "/moyuren",
    response_model=MoyurenImageResponse,
    responses={404: {"model": ErrorResponse}},
)
async def get_moyuren(request: Request) -> JSONResponse:
    """Get the latest moyuren calendar image metadata.

    Returns simplified metadata of the latest generated image including:
    - date: Image date in YYYY-MM-DD format
    - updated: Generation time in RFC3339 format (e.g., 2026-02-01T07:22:32+08:00)
    - updated_at: Generation timestamp in milliseconds (13 digits)
    - image: Full URL to the image file

    Raises:
        404: If no image has been generated yet.
    """
    logger = logging.getLogger(__name__)

    # Get state file path from config
    config = request.app.state.config
    state_path = Path(config.paths.state_path)

    # Get valid state data (regenerates if incompatible)
    state_data, error = await _get_valid_state(request, state_path, logger)
    if error:
        return error

    # Build full image URL
    base_domain = config.server.base_domain.rstrip("/")
    filename = state_data["filename"]
    image_url = f"{base_domain}/static/{filename}"

    # Build response with new format
    response_data = {
        "date": state_data["date"],
        "updated": state_data["updated"],
        "updated_at": state_data["updated_at"],
        "image": image_url,
    }

    logger.info(f"Retrieved moyuren image metadata: {filename}")
    return JSONResponse(
        content=response_data,
        status_code=status.HTTP_200_OK,
    )


@router.get(
    "/moyuren/detail",
    response_model=MoyurenDetailResponse,
    responses={404: {"model": ErrorResponse}},
)
async def get_moyuren_detail(request: Request) -> JSONResponse:
    """Get the detailed content of the latest moyuren calendar image.

    Returns detailed content information including:
    - date: Image date in YYYY-MM-DD format
    - updated: Generation time in RFC3339 format (e.g., 2026-02-01T07:22:32+08:00)
    - updated_at: Generation timestamp in milliseconds (13 digits)
    - image: Full URL to the image file
    - weekday: Weekday in Chinese (e.g., 星期日)
    - lunar_date: Lunar calendar date
    - fun_content: Fun content (joke, quote, etc.)
    - countdowns: Holiday countdowns
    - is_crazy_thursday: Whether it's Thursday
    - kfc_content: KFC Crazy Thursday content (only on Thursday)

    Raises:
        404: If no image has been generated yet.
    """
    logger = logging.getLogger(__name__)

    # Get state file path from config
    config = request.app.state.config
    state_path = Path(config.paths.state_path)

    # Get valid state data (regenerates if incompatible)
    state_data, error = await _get_valid_state(request, state_path, logger)
    if error:
        return error

    # Build full image URL
    base_domain = config.server.base_domain.rstrip("/")
    filename = state_data["filename"]
    image_url = f"{base_domain}/static/{filename}"

    # Build response with detailed content
    response_data = {
        "date": state_data["date"],
        "updated": state_data["updated"],
        "updated_at": state_data["updated_at"],
        "image": image_url,
        "weekday": state_data.get("weekday", ""),
        "lunar_date": state_data.get("lunar_date", ""),
        "fun_content": state_data.get("fun_content"),
        "countdowns": state_data.get("countdowns", []),
        "is_crazy_thursday": state_data.get("is_crazy_thursday", False),
        "kfc_content": state_data.get("kfc_content"),
        # Full rendering data fields
        "date_info": state_data.get("date_info"),
        "weekend": state_data.get("weekend"),
        "solar_term": state_data.get("solar_term"),
        "guide": state_data.get("guide"),
        "news_list": state_data.get("news_list"),
        "news_meta": state_data.get("news_meta"),
        "holidays": state_data.get("holidays"),
        "kfc_content_full": state_data.get("kfc_content_full"),
        "stock_indices": state_data.get("stock_indices"),
    }

    logger.info(f"Retrieved moyuren detail for: {state_data['date']}")
    return JSONResponse(
        content=response_data,
        status_code=status.HTTP_200_OK,
    )


@router.get(
    "/moyuren/latest",
    response_class=FileResponse,
    responses={404: {"model": ErrorResponse}},
)
async def get_moyuren_latest(request: Request) -> FileResponse:
    """Get the latest moyuren calendar image file directly.

    Returns the actual JPEG image file instead of JSON metadata.
    This endpoint is useful for embedding the image directly in HTML or markdown.

    Raises:
        404: If no image has been generated yet or image file not found.
    """
    logger = logging.getLogger(__name__)

    # Get state file path from config
    config = request.app.state.config
    state_path = Path(config.paths.state_path)

    # Get valid state data (regenerates if incompatible)
    state_data, error = await _get_valid_state(request, state_path, logger)
    if error:
        return error

    # Build image file path with security validation
    static_dir = Path(config.paths.static_dir)
    filename = state_data["filename"]

    # Validate filename to prevent path traversal
    if "/" in filename or "\\" in filename or ".." in filename:
        logger.error(f"Invalid filename with path separators: {filename}")
        return JSONResponse(
            content=error_response(
                code=ErrorCode.STORAGE_READ_FAILED,
                message="Invalid filename",
                detail="Filename contains invalid characters",
            ),
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    image_path = static_dir / filename

    # Ensure resolved path is still within static_dir
    try:
        resolved_path = image_path.resolve()
        resolved_static = static_dir.resolve()
        if not str(resolved_path).startswith(str(resolved_static)):
            logger.error(f"Path traversal attempt: {filename}")
            return JSONResponse(
                content=error_response(
                    code=ErrorCode.STORAGE_READ_FAILED,
                    message="Invalid file path",
                    detail="File path is outside static directory",
                ),
                status_code=status.HTTP_400_BAD_REQUEST,
            )
    except (OSError, ValueError) as e:
        logger.error(f"Failed to resolve path: {e}")
        return JSONResponse(
            content=error_response(
                code=ErrorCode.STORAGE_READ_FAILED,
                message="Invalid file path",
                detail=str(e),
            ),
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    # Check if image file exists and is a regular file
    if not image_path.is_file():
        logger.error(f"Image file not found or not a regular file: {image_path}")
        return JSONResponse(
            content=error_response(
                code=ErrorCode.STORAGE_NOT_FOUND,
                message="Image file not found",
                detail=f"File {filename} does not exist or is not a regular file",
            ),
            status_code=status.HTTP_404_NOT_FOUND,
        )

    logger.info(f"Serving latest moyuren image: {filename}")
    return FileResponse(
        path=image_path,
        media_type="image/jpeg",
        filename=filename,
        headers={
            "Cache-Control": "no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )
