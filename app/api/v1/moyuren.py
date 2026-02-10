"""Moyuren image API endpoints."""

import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Query, Request, status
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response

from app.core.errors import ErrorCode, StorageError, error_response, get_http_status
from app.models.schemas import ErrorResponse
from app.services.calendar import today_business
from app.services.generator import GenerationBusyError, generate_and_save_image

router = APIRouter(prefix="/api/v1", tags=["moyuren"])


def _build_image_url(base_domain: str, filename: str) -> str:
    """Build full image URL from base domain and filename."""
    return f"{base_domain.rstrip('/')}/static/{filename}"


def _get_filename_for_template(images: dict[str, str], template: str | None) -> str | None:
    """Get filename for specified template from images mapping.

    Args:
        images: Images mapping {template_name: filename}
        template: Template name, None means use first available

    Returns:
        Filename or None if not found
    """
    if not images:
        return None

    if template is None:
        # Return first available image
        return next(iter(images.values()), None)

    # Return specific template image
    return images.get(template)


def _build_cache_headers(target_date: date, updated_at: int) -> dict[str, str]:
    """Build HTTP cache headers based on date.

    Args:
        target_date: Target date for the data
        updated_at: Update timestamp in milliseconds

    Returns:
        Dictionary of cache headers
    """
    today = today_business()

    if target_date < today:
        # History data - immutable
        return {
            "Cache-Control": "public, max-age=31536000, immutable",
        }
    else:
        # Today's data - use ETag and Last-Modified
        etag = f'"{updated_at}"'
        last_modified = datetime.fromtimestamp(updated_at / 1000, tz=timezone.utc).strftime(
            "%a, %d %b %Y %H:%M:%S GMT"
        )
        return {
            "Cache-Control": "public, max-age=300, must-revalidate",
            "ETag": etag,
            "Last-Modified": last_modified,
        }


def _check_not_modified(request: Request, updated_at: int) -> bool:
    """Check if client cache is still valid (304 Not Modified).

    Args:
        request: FastAPI request object
        updated_at: Update timestamp in milliseconds

    Returns:
        True if client cache is valid (should return 304)
    """
    etag = f'"{updated_at}"'

    # Check If-None-Match header
    if_none_match = request.headers.get("If-None-Match")
    if if_none_match and if_none_match == etag:
        return True

    # Check If-Modified-Since header
    if_modified_since = request.headers.get("If-Modified-Since")
    if if_modified_since:
        try:
            client_time = datetime.strptime(if_modified_since, "%a, %d %b %Y %H:%M:%S GMT")
            server_time = datetime.fromtimestamp(updated_at / 1000, tz=timezone.utc).replace(tzinfo=None)
            if client_time >= server_time:
                return True
        except ValueError:
            pass

    return False


def _build_simple_response(data: dict, base_domain: str, template: str | None) -> dict:
    """Build simple response (encode=json, detail=false).

    Returns:
        - date: YYYY-MM-DD
        - updated: YYYY/MM/DD HH:MM:SS
        - updated_at: milliseconds timestamp
        - image: full URL
    """
    images = data.get("images", {})
    filename = _get_filename_for_template(images, template)
    if not filename:
        raise StorageError(
            message=f"No image found for template: {template or 'default'}",
            code=ErrorCode.API_TEMPLATE_NOT_FOUND,
        )

    return {
        "date": data["date"],
        "updated": data["updated"],
        "updated_at": data["updated_at"],
        "image": _build_image_url(base_domain, filename),
    }


def _build_detail_response(data: dict, base_domain: str, template: str | None) -> dict:
    """Build detailed response (encode=json, detail=true).

    Returns all fields from data file plus image URL.
    """
    simple = _build_simple_response(data, base_domain, template)

    # Add all optional fields
    detail_fields = {
        "weekday": data.get("weekday", ""),
        "lunar_date": data.get("lunar_date", ""),
        "fun_content": data.get("fun_content"),
        "is_crazy_thursday": data.get("is_crazy_thursday", False),
        "kfc_content": data.get("kfc_content"),
        "date_info": data.get("date_info"),
        "weekend": data.get("weekend"),
        "solar_term": data.get("solar_term"),
        "guide": data.get("guide"),
        "news_list": data.get("news_list"),
        "news_meta": data.get("news_meta"),
        "holidays": data.get("holidays"),
        "kfc_content_full": data.get("kfc_content_full"),
        "stock_indices": data.get("stock_indices"),
    }

    return {**simple, **detail_fields}


def _build_text_response(data: dict, base_domain: str, template: str | None) -> str:
    """Build plain text response (encode=text).

    Format:
        日期: 2026-02-10
        更新时间: 2026/02/10 07:22:32
        图片: https://example.com/static/moyuren_20260210_072232.jpg
    """
    simple = _build_simple_response(data, base_domain, template)
    return (
        f"日期: {simple['date']}\n"
        f"更新时间: {simple['updated']}\n"
        f"图片: {simple['image']}\n"
    )


def _build_markdown_response(data: dict, base_domain: str, template: str | None) -> str:
    """Build markdown response (encode=markdown).

    Format:
        # 摸鱼日历 - 2026-02-10

        **更新时间**: 2026/02/10 07:22:32

        ![摸鱼日历](https://example.com/static/moyuren_20260210_072232.jpg)
    """
    simple = _build_simple_response(data, base_domain, template)
    return (
        f"# 摸鱼日历 - {simple['date']}\n\n"
        f"**更新时间**: {simple['updated']}\n\n"
        f"![摸鱼日历]({simple['image']})\n"
    )


async def _load_data_for_date(
    request: Request,
    target_date: date,
    logger: logging.Logger,
) -> tuple[dict | None, JSONResponse | None]:
    """Load and validate data file for specified date.

    Args:
        request: FastAPI request object
        target_date: Target date to load
        logger: Logger instance

    Returns:
        Tuple of (data, error_response). If successful, error_response is None.
    """
    config = request.app.state.config
    cache_dir = Path(config.paths.cache_dir)
    data_file = cache_dir / "data" / f"{target_date.isoformat()}.json"

    # For today's date, trigger generation if file doesn't exist
    today = today_business()
    if target_date == today and not data_file.exists():
        logger.info(f"Data file not found for today ({target_date}), triggering generation...")
        try:
            await generate_and_save_image(request.app)
        except GenerationBusyError:
            logger.info("Generation in progress, returning 503 with Retry-After")
            return None, JSONResponse(
                content=error_response(
                    code=ErrorCode.GENERATION_BUSY,
                    message="Image generation in progress, another process is generating the image",
                ),
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                headers={"Retry-After": "5"},
            )
        except Exception as e:
            logger.error(f"On-demand image generation failed: {e}")
            return None, JSONResponse(
                content=error_response(
                    code=ErrorCode.GENERATION_FAILED,
                    message=f"Image generation failed: {e}",
                ),
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    # Read data file
    if not data_file.exists():
        logger.warning(f"Data file not found for date: {target_date}")
        return None, JSONResponse(
            content=error_response(
                code=ErrorCode.API_DATA_NOT_FOUND,
                message=f"No data available for date: {target_date.isoformat()}",
            ),
            status_code=status.HTTP_404_NOT_FOUND,
        )

    try:
        with data_file.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except OSError as e:
        logger.error(f"Failed to read data file: {e}")
        return None, JSONResponse(
            content=error_response(
                code=ErrorCode.STORAGE_READ_FAILED,
                message=f"Failed to read data file: {e}",
            ),
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse data file: {e}")
        return None, JSONResponse(
            content=error_response(
                code=ErrorCode.STORAGE_READ_FAILED,
                message=f"Invalid data file: {e}",
            ),
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    # Validate data structure
    if not isinstance(data, dict):
        logger.error(f"Data file has invalid format: expected dict, got {type(data).__name__}")
        return None, JSONResponse(
            content=error_response(
                code=ErrorCode.STORAGE_READ_FAILED,
                message=f"Invalid data file: expected dict, got {type(data).__name__}",
            ),
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    required_fields = ["date", "updated", "updated_at", "images"]
    missing_fields = [f for f in required_fields if f not in data]
    if missing_fields:
        logger.error(f"Data file missing required fields: {missing_fields}")
        return None, JSONResponse(
            content=error_response(
                code=ErrorCode.STORAGE_READ_FAILED,
                message=f"Invalid data file: missing required fields: {missing_fields}",
            ),
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    return data, None



def _handle_json_response(
    data: dict,
    base_domain: str,
    template: str | None,
    detail: bool,
    cache_headers: dict[str, str],
    target_date: date,
    logger: logging.Logger,
) -> JSONResponse:
    """Handle JSON format response (encode=json)."""
    if detail:
        response_data = _build_detail_response(data, base_domain, template)
    else:
        response_data = _build_simple_response(data, base_domain, template)

    logger.info(f"Retrieved moyuren data for {target_date} (encode=json, detail={detail})")
    return JSONResponse(
        content=response_data,
        status_code=status.HTTP_200_OK,
        headers=cache_headers,
    )


def _handle_text_response(
    data: dict,
    base_domain: str,
    template: str | None,
    cache_headers: dict[str, str],
    target_date: date,
    logger: logging.Logger,
) -> PlainTextResponse:
    """Handle plain text format response (encode=text)."""
    text_content = _build_text_response(data, base_domain, template)
    logger.info(f"Retrieved moyuren data for {target_date} (encode=text)")
    return PlainTextResponse(
        content=text_content,
        status_code=status.HTTP_200_OK,
        headers=cache_headers,
    )


def _handle_markdown_response(
    data: dict,
    base_domain: str,
    template: str | None,
    cache_headers: dict[str, str],
    target_date: date,
    logger: logging.Logger,
) -> PlainTextResponse:
    """Handle markdown format response (encode=markdown)."""
    markdown_content = _build_markdown_response(data, base_domain, template)
    logger.info(f"Retrieved moyuren data for {target_date} (encode=markdown)")
    return PlainTextResponse(
        content=markdown_content,
        status_code=status.HTTP_200_OK,
        headers={**cache_headers, "Content-Type": "text/markdown; charset=utf-8"},
    )


def _handle_image_response(
    data: dict,
    cache_dir: str,
    template: str | None,
    cache_headers: dict[str, str],
    target_date: date,
    logger: logging.Logger,
) -> FileResponse | JSONResponse:
    """Handle image file response (encode=image)."""
    images = data.get("images", {})
    filename = _get_filename_for_template(images, template)
    if not filename:
        return JSONResponse(
            content=error_response(
                code=ErrorCode.API_TEMPLATE_NOT_FOUND,
                message=f"No image found for template: {template or 'default'}",
            ),
            status_code=status.HTTP_404_NOT_FOUND,
        )

    # Build and validate image path
    images_dir = Path(cache_dir) / "images"

    # Validate filename to prevent path traversal
    if "/" in filename or "\\" in filename or ".." in filename:
        logger.error(f"Invalid filename with path separators: {filename}")
        return JSONResponse(
            content=error_response(
                code=ErrorCode.STORAGE_READ_FAILED,
                message="Invalid filename: contains invalid characters",
            ),
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    image_path = images_dir / filename

    # Ensure resolved path is still within images_dir
    try:
        resolved_path = image_path.resolve()
        resolved_images = images_dir.resolve()
        if not str(resolved_path).startswith(str(resolved_images)):
            logger.error(f"Path traversal attempt: {filename}")
            return JSONResponse(
                content=error_response(
                    code=ErrorCode.STORAGE_READ_FAILED,
                    message="Invalid file path: outside images directory",
                ),
                status_code=status.HTTP_400_BAD_REQUEST,
            )
    except (OSError, ValueError) as e:
        logger.error(f"Failed to resolve path: {e}")
        return JSONResponse(
            content=error_response(
                code=ErrorCode.STORAGE_READ_FAILED,
                message=f"Invalid file path: {e}",
            ),
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    # Check if image file exists
    if not image_path.is_file():
        logger.error(f"Image file not found: {image_path}")
        return JSONResponse(
            content=error_response(
                code=ErrorCode.STORAGE_NOT_FOUND,
                message=f"Image file not found: {filename}",
            ),
            status_code=status.HTTP_404_NOT_FOUND,
        )

    logger.info(f"Serving moyuren image for {target_date}: {filename}")
    return FileResponse(
        path=image_path,
        media_type="image/jpeg",
        filename=filename,
        headers=cache_headers,
    )


@router.get(
    "/moyuren",
    responses={
        200: {"description": "Success"},
        304: {"description": "Not Modified"},
        400: {"model": ErrorResponse, "description": "Invalid parameters"},
        404: {"model": ErrorResponse, "description": "Data not found"},
        503: {"model": ErrorResponse, "description": "Generation in progress"},
    },
)
async def get_moyuren(
    request: Request,
    date: str | None = Query(None, description="Target date in YYYY-MM-DD format, defaults to today"),
    encode: str = Query("json", description="Output format: json, text, markdown, image"),
    template: str | None = Query(None, description="Template name, defaults to first available"),
    detail: bool = Query(False, description="Include detailed fields (only for encode=json)"),
) -> Response:
    """Get moyuren calendar image data or file.

    Unified endpoint supporting multiple output formats and query parameters.

    Query Parameters:
        - date: Target date (YYYY-MM-DD), defaults to today
        - encode: Output format (json/text/markdown/image), defaults to json
        - template: Template name, defaults to first available
        - detail: Include detailed fields (only for encode=json), defaults to false

    HTTP Caching:
        - History dates (< today): Cache-Control: immutable
        - Today's date: ETag + Last-Modified, supports 304 Not Modified

    Returns:
        - encode=json: JSON response with image metadata
        - encode=text: Plain text response
        - encode=markdown: Markdown formatted response
        - encode=image: JPEG image file
    """
    logger = logging.getLogger(__name__)

    # Validate encode parameter
    valid_encodes = ["json", "text", "markdown", "image"]
    if encode not in valid_encodes:
        return JSONResponse(
            content=error_response(
                code=ErrorCode.API_INVALID_ENCODE,
                message=f"Invalid encode parameter: {encode}, must be one of {valid_encodes}",
            ),
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    # Parse and validate date parameter
    if date is None:
        target_date = today_business()
    else:
        try:
            target_date = datetime.strptime(date, "%Y-%m-%d").date()
        except ValueError:
            return JSONResponse(
                content=error_response(
                    code=ErrorCode.API_INVALID_DATE,
                    message=f"Invalid date format: {date}, expected YYYY-MM-DD",
                ),
                status_code=status.HTTP_400_BAD_REQUEST,
            )

    # Load data for target date
    data, error = await _load_data_for_date(request, target_date, logger)
    if error:
        return error

    # Check 304 Not Modified for today's data
    today = today_business()
    if target_date == today and _check_not_modified(request, data["updated_at"]):
        cache_headers = _build_cache_headers(target_date, data["updated_at"])
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=cache_headers)

    # Build cache headers
    cache_headers = _build_cache_headers(target_date, data["updated_at"])
    config = request.app.state.config
    base_domain = config.server.base_domain

    # Handle different output formats
    try:
        if encode == "image":
            return _handle_image_response(data, config.paths.cache_dir, template, cache_headers, target_date, logger)
        elif encode == "text":
            return _handle_text_response(data, base_domain, template, cache_headers, target_date, logger)
        elif encode == "markdown":
            return _handle_markdown_response(data, base_domain, template, cache_headers, target_date, logger)
        else:
            return _handle_json_response(data, base_domain, template, detail, cache_headers, target_date, logger)
    except StorageError as e:
        logger.error(f"Storage error: {e.message}")
        return JSONResponse(
            content=error_response(code=e.code, message=e.message),
            status_code=get_http_status(e.code),
        )
