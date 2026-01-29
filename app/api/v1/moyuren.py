"""Moyuren image API endpoints."""

import asyncio
import json
import logging
from pathlib import Path

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse

from app.core.errors import ErrorCode, error_response
from app.models.schemas import ErrorResponse, MoyurenResponse
from app.services.generator import GenerationBusyError, generate_and_save_image

router = APIRouter(prefix="/api/v1", tags=["moyuren"])


@router.get(
    "/moyuren",
    response_model=MoyurenResponse,
    responses={404: {"model": ErrorResponse}},
)
async def get_moyuren(request: Request) -> JSONResponse:
    """Get the latest moyuren calendar image.

    Returns metadata of the latest generated image including:
    - date: Image date in YYYY-MM-DD format
    - timestamp: Generation timestamp in ISO format
    - image: Full URL to the image file

    Raises:
        404: If no image has been generated yet.
    """
    logger = logging.getLogger(__name__)

    # Get state file path from config
    config = request.app.state.config
    state_path = Path(config.paths.state_path)

    # Read state file
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

    try:
        with state_path.open("r", encoding="utf-8") as f:
            state_data = json.load(f)
    except FileNotFoundError:
        logger.error("State file disappeared after generation")
        return JSONResponse(
            content=error_response(
                code=ErrorCode.STORAGE_NOT_FOUND,
                message="No image available",
                detail="State file not found after generation attempt",
            ),
            status_code=status.HTTP_404_NOT_FOUND,
        )
    except OSError as e:
        logger.error(f"Failed to read state file: {e}")
        return JSONResponse(
            content=error_response(
                code=ErrorCode.STORAGE_READ_FAILED,
                message="Failed to read state file",
                detail=str(e),
            ),
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse state file: {e}")
        return JSONResponse(
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
        return JSONResponse(
            content=error_response(
                code=ErrorCode.STORAGE_READ_FAILED,
                message="Invalid state file",
                detail=f"Expected dict, got {type(state_data).__name__}",
            ),
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    required_fields = ["filename", "date", "timestamp"]
    missing_fields = [f for f in required_fields if not state_data.get(f)]
    if missing_fields:
        logger.warning(f"State file missing required fields: {missing_fields}")
        return JSONResponse(
            content=error_response(
                code=ErrorCode.STORAGE_NOT_FOUND,
                message="No image available",
                detail=f"State file missing required fields: {missing_fields}",
            ),
            status_code=status.HTTP_404_NOT_FOUND,
        )

    # Build full image URL
    base_domain = config.server.base_domain.rstrip("/")
    filename = state_data["filename"]
    image_url = f"{base_domain}/static/{filename}"

    # Build response
    response_data = {
        "date": state_data["date"],
        "timestamp": state_data["timestamp"],
        "image": image_url,
    }

    logger.info(f"Retrieved moyuren image: {filename}")
    return JSONResponse(
        content=response_data,
        status_code=status.HTTP_200_OK,
    )
