"""Moyuren image API endpoints."""

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse

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

    required_fields = ["filename", "date"]
    missing_fields = [f for f in required_fields if not state_data.get(f)]
    if missing_fields:
        logger.warning(f"State file missing required fields: {missing_fields}")
        return None, JSONResponse(
            content=error_response(
                code=ErrorCode.STORAGE_NOT_FOUND,
                message="No image available",
                detail=f"State file missing required fields: {missing_fields}",
            ),
            status_code=status.HTTP_404_NOT_FOUND,
        )

    # Migrate old state format: convert timestamp to updated/updated_at
    if "updated" not in state_data or "updated_at" not in state_data:
        timestamp_str = state_data.get("timestamp", "")
        if timestamp_str:
            try:
                dt = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
                state_data["updated"] = dt.strftime("%Y/%m/%d %H:%M:%S")
                state_data["updated_at"] = int(dt.timestamp() * 1000)
            except ValueError:
                # Fallback to current time if parsing fails
                now = datetime.now()
                state_data["updated"] = now.strftime("%Y/%m/%d %H:%M:%S")
                state_data["updated_at"] = int(now.timestamp() * 1000)
        else:
            now = datetime.now()
            state_data["updated"] = now.strftime("%Y/%m/%d %H:%M:%S")
            state_data["updated_at"] = int(now.timestamp() * 1000)

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
    - updated: Generation time in YYYY/MM/DD HH:MM:SS format
    - updated_at: Generation timestamp in milliseconds (13 digits)
    - image: Full URL to the image file

    Raises:
        404: If no image has been generated yet.
    """
    logger = logging.getLogger(__name__)

    # Get state file path from config
    config = request.app.state.config
    state_path = Path(config.paths.state_path)

    # Ensure state file exists
    if error := await _ensure_state_file_exists(request, state_path, logger):
        return error

    # Read state file
    state_data, error = await _read_state_file(state_path, logger)
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
    - updated: Generation time in YYYY/MM/DD HH:MM:SS format
    - updated_at: Generation timestamp in milliseconds (13 digits)
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

    # Ensure state file exists
    if error := await _ensure_state_file_exists(request, state_path, logger):
        return error

    # Read state file
    state_data, error = await _read_state_file(state_path, logger)
    if error:
        return error

    # Build response with detailed content
    response_data = {
        "date": state_data["date"],
        "updated": state_data["updated"],
        "updated_at": state_data["updated_at"],
        "weekday": state_data.get("weekday", ""),
        "lunar_date": state_data.get("lunar_date", ""),
        "fun_content": state_data.get("fun_content"),
        "countdowns": state_data.get("countdowns", []),
        "is_crazy_thursday": state_data.get("is_crazy_thursday", False),
        "kfc_content": state_data.get("kfc_content"),
    }

    logger.info(f"Retrieved moyuren detail for: {state_data['date']}")
    return JSONResponse(
        content=response_data,
        status_code=status.HTTP_200_OK,
    )
