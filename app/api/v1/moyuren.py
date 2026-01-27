"""Moyuren image API endpoints."""

import json
import logging
from pathlib import Path

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse

from app.core.errors import ErrorCode, error_response
from app.models.schemas import ErrorResponse, MoyurenResponse

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
        logger.warning("State file not found")
        return JSONResponse(
            content=error_response(
                code=ErrorCode.STORAGE_NOT_FOUND,
                message="No image available",
                detail="State file not found. Please generate an image first.",
            ),
            status_code=status.HTTP_404_NOT_FOUND,
        )

    try:
        with state_path.open("r", encoding="utf-8") as f:
            state_data = json.load(f)
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
