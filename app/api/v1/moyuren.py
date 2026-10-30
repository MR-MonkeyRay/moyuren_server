"""Moyuren image API endpoints."""

import asyncio
import hashlib
import logging
from datetime import UTC, date, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Annotated, Literal, get_args

from fastapi import APIRouter, Query, Request, status
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response

from app.core.config import AppConfig
from app.core.errors import ErrorCode, StorageError, error_response, get_http_status
from app.models.schemas import DailyDataFile, ErrorResponse, ImageVariant
from app.services.calendar import today_business
from app.services.generator import GenerationBusyError, generate_and_save_image
from app.services.variants import ensure_variant, image_width

router = APIRouter(prefix="/api/v1", tags=["moyuren"])
EncodeFormat = Literal["json", "text", "markdown", "image", "url"]
ImageFormat = Literal["jpeg", "webp"]


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


def _cache_etag(updated_at: int, identity: str | None = None) -> str:
    """Build a stable ETag, optionally scoped to a selected image variant."""
    if identity is None:
        return f'"{updated_at}"'
    digest = hashlib.sha256(identity.encode()).hexdigest()[:16]
    return f'"{updated_at}-{digest}"'


def _build_cache_headers(
    target_date: date, updated_at: int, identity: str | None = None
) -> dict[str, str]:
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
        etag = _cache_etag(updated_at, identity)
        last_modified = datetime.fromtimestamp(updated_at / 1000, tz=UTC).strftime(
            "%a, %d %b %Y %H:%M:%S GMT"
        )
        return {
            "Cache-Control": "public, max-age=300, must-revalidate",
            "ETag": etag,
            "Last-Modified": last_modified,
        }


def _check_not_modified(
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
    etag = _cache_etag(updated_at, identity)

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


def _build_simple_response(
    data: DailyDataFile, base_domain: str, template: str | None
) -> dict[str, object]:
    """Build simple response (encode=json, detail=false).

    Returns:
        - date: YYYY-MM-DD
        - updated: YYYY/MM/DD HH:MM:SS
        - updated_at: milliseconds timestamp
        - image: full URL
    """
    filename = _get_filename_for_template(data.images, template)
    if not filename:
        raise StorageError(
            message=f"No image found for template: {template or 'default'}",
            code=ErrorCode.API_TEMPLATE_NOT_FOUND,
        )

    variants = _variant_payload(data, base_domain, template)
    return {
        "date": data.date,
        "updated": data.updated,
        "updated_at": data.updated_at,
        "image": _build_image_url(base_domain, filename),
        "variants": variants,
    }


def _variant_payload(
    data: DailyDataFile, base_domain: str, template: str | None
) -> list[dict[str, object]]:
    """Build public variant metadata with absolute static URLs."""
    template_name = template or next(iter(data.images), None)
    if template_name is None or data.image_variants is None:
        return []
    return [
        {
            "image": _build_image_url(base_domain, variant.filename),
            "format": variant.format,
            "width": variant.width,
        }
        for variant in data.image_variants.get(template_name, [])
    ]


def _build_detail_response(
    data: DailyDataFile, base_domain: str, template: str | None
) -> dict[str, object]:
    """Build detailed response (encode=json, detail=true).

    Returns the established public detail fields plus image URL. Internal
    ``schema_version`` metadata is intentionally excluded from API output.
    """
    simple = _build_simple_response(data, base_domain, template)
    stored = data.model_dump(
        mode="json", exclude={"schema_version"}, exclude_unset=True
    )

    # Add all optional fields
    detail_fields = {
        "weekday": stored.get("weekday", ""),
        "lunar_date": stored.get("lunar_date", ""),
        "fun_content": stored.get("fun_content"),
        "is_crazy_thursday": stored.get("is_crazy_thursday", False),
        "kfc_content": stored.get("kfc_content"),
        "date_info": stored.get("date_info"),
        "weekend": stored.get("weekend"),
        "solar_term": stored.get("solar_term"),
        "guide": stored.get("guide"),
        "news_list": stored.get("news_list"),
        "news_meta": stored.get("news_meta"),
        "holidays": stored.get("holidays"),
        "kfc_content_full": stored.get("kfc_content_full"),
        "stock_indices": stored.get("stock_indices"),
        "gold_price": stored.get("gold_price"),
        "is_stale": bool(stored.get("is_stale")),
        "source_date": stored.get("source_date"),
    }

    return {**simple, **detail_fields}


def _build_text_response(
    data: DailyDataFile, base_domain: str, template: str | None
) -> str:
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


def _build_markdown_response(
    data: DailyDataFile, base_domain: str, template: str | None
) -> str:
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


async def _generate_on_demand(
    request: Request, logger: logging.Logger, template_name: str | None = None
) -> JSONResponse | None:
    """Generate images on demand and translate generation errors to responses."""
    try:
        await generate_and_save_image(request.app, template_name=template_name)
    except GenerationBusyError:
        logger.info("Generation in progress, returning 503 with Retry-After")
        return JSONResponse(
            content=error_response(
                code=ErrorCode.GENERATION_BUSY,
                message="Image generation in progress, another process is generating the image",
            ),
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            headers={"Retry-After": "5"},
        )
    except Exception as e:
        logger.error("On-demand image generation failed: %s", e)
        return JSONResponse(
            content=error_response(
                code=ErrorCode.GENERATION_FAILED,
                message="Image generation failed, please try again later",
            ),
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    return None


async def _load_data_for_date(
    request: Request,
    target_date: date,
    logger: logging.Logger,
    template: str | None = None,
    *,
    allow_generation: bool = True,
) -> tuple[DailyDataFile | None, JSONResponse | None]:
    """Load and validate data file for specified date.

    Args:
        request: FastAPI request object
        target_date: Target date to load
        logger: Logger instance
        template: Requested template, None means use first available
        allow_generation: Whether this request may still attempt generation

    Returns:
        Tuple of (data, error_response). If successful, error_response is None.
    """
    config = request.app.state.config
    cache_dir = Path(config.paths.cache_dir)
    data_file = cache_dir / "data" / f"{target_date.isoformat()}.json"

    # For today's date, trigger generation if file doesn't exist
    today = today_business()
    if target_date == today and not data_file.exists() and allow_generation:
        logger.info("Data file not found for today (%s), triggering generation...", target_date)
        error = await _generate_on_demand(request, logger)
        if error is not None:
            return None, error
        allow_generation = False

    # Read data file
    if not data_file.exists():
        logger.warning("Data file not found for date: %s", target_date)
        return None, JSONResponse(
            content=error_response(
                code=ErrorCode.API_DATA_NOT_FOUND,
                message=f"No data available for date: {target_date.isoformat()}",
            ),
            status_code=status.HTTP_404_NOT_FOUND,
        )

    try:
        daily_data = DailyDataFile.load(data_file)
    except StorageError as e:
        logger.error("Failed to load data file: %s", e.message)
        cause = e.__cause__
        errors = getattr(cause, "errors", None)
        invalid_image = bool(
            callable(errors)
            and any(error.get("loc", ())[:1] == ("images",) for error in errors())
        )
        return None, JSONResponse(
            content=error_response(
                code=ErrorCode.STORAGE_READ_FAILED,
                message=(
                    "Invalid filename: contains invalid characters"
                    if invalid_image
                    else "Invalid data file format"
                ),
            ),
            status_code=(
                status.HTTP_400_BAD_REQUEST
                if invalid_image
                else status.HTTP_500_INTERNAL_SERVER_ERROR
            ),
        )

    if target_date == today:
        images = daily_data.images
        filename = _get_filename_for_template(images, template)
        known_template = template in images if template is not None else bool(images)
        if not known_template:
            templates = config.get_templates_config().items
            known_template = any(template is None or item.name == template for item in templates)

        if known_template:
            _, image_error = _get_image_path(
                filename or "", config.paths.cache_dir, logger
            )
            if image_error is not None:
                if image_error.status_code == status.HTTP_404_NOT_FOUND and allow_generation:
                    logger.info(
                        "Image file not found for today (%s), triggering generation...", target_date
                    )
                    template_name = template if template is not None else next(iter(images), None)
                    error = await _generate_on_demand(request, logger, template_name=template_name)
                    if error is not None:
                        return None, error
                    return await _load_data_for_date(
                        request, target_date, logger, template, allow_generation=False
                    )
                return None, image_error

    return daily_data, None



def _handle_json_response(
    data: DailyDataFile,
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

    logger.info(
        "Retrieved moyuren data for %s (encode=json, detail=%s)", target_date, detail
    )
    return JSONResponse(
        content=response_data,
        status_code=status.HTTP_200_OK,
        headers=cache_headers,
    )


def _handle_text_response(
    data: DailyDataFile,
    base_domain: str,
    template: str | None,
    cache_headers: dict[str, str],
    target_date: date,
    logger: logging.Logger,
) -> PlainTextResponse:
    """Handle plain text format response (encode=text)."""
    text_content = _build_text_response(data, base_domain, template)
    logger.info("Retrieved moyuren data for %s (encode=text)", target_date)
    return PlainTextResponse(
        content=text_content,
        status_code=status.HTTP_200_OK,
        headers=cache_headers,
    )


def _handle_markdown_response(
    data: DailyDataFile,
    base_domain: str,
    template: str | None,
    cache_headers: dict[str, str],
    target_date: date,
    logger: logging.Logger,
) -> PlainTextResponse:
    """Handle markdown format response (encode=markdown)."""
    markdown_content = _build_markdown_response(data, base_domain, template)
    logger.info("Retrieved moyuren data for %s (encode=markdown)", target_date)
    return PlainTextResponse(
        content=markdown_content,
        status_code=status.HTTP_200_OK,
        headers={**cache_headers, "Content-Type": "text/markdown; charset=utf-8"},
    )


def _get_image_path(
    filename: str, cache_dir: str, logger: logging.Logger
) -> tuple[Path | None, JSONResponse | None]:
    """Resolve an existing image file within the images directory."""
    # Build and validate image path
    images_dir = Path(cache_dir) / "images"

    # Validate filename to prevent path traversal
    if "/" in filename or "\\" in filename or ".." in filename:
        logger.error("Invalid filename with path separators: %s", filename)
        return None, JSONResponse(
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
        if not resolved_path.is_relative_to(resolved_images):
            logger.error("Path traversal attempt: %s", filename)
            return None, JSONResponse(
                content=error_response(
                    code=ErrorCode.STORAGE_READ_FAILED,
                    message="Invalid file path: outside images directory",
                ),
                status_code=status.HTTP_400_BAD_REQUEST,
            )
    except (OSError, ValueError) as e:
        logger.error("Failed to resolve path: %s", e)
        return None, JSONResponse(
            content=error_response(
                code=ErrorCode.STORAGE_READ_FAILED,
                message="Invalid file path",
            ),
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    # Check if image file exists
    if not image_path.is_file():
        logger.error("Image file not found: %s", image_path)
        return None, JSONResponse(
            content=error_response(
                code=ErrorCode.STORAGE_NOT_FOUND,
                message=f"Image file not found: {filename}",
            ),
            status_code=status.HTTP_404_NOT_FOUND,
        )

    return image_path, None


def _configured_formats(render_config: object) -> list[str]:
    """Return configured formats while tolerating legacy test doubles."""
    formats = getattr(render_config, "output_formats", ["jpeg"])
    if not isinstance(formats, list):
        return ["jpeg"]
    return formats


def _select_variant_width(
    requested_width: int | None, original_width: int, configured_widths: list[int]
) -> int:
    """Choose the full width or nearest configured width for a request."""
    if requested_width is None:
        return original_width
    available = sorted({width for width in configured_widths if width < original_width} | {original_width})
    return next((width for width in available if width >= requested_width), available[-1])


def _recorded_variant(
    data: DailyDataFile, template: str | None, image_format: ImageFormat, width: int
) -> ImageVariant | None:
    """Find an exact recorded variant for the selected template."""
    template_name = template or next(iter(data.images), None)
    if template_name is None or data.image_variants is None:
        return None
    return next(
        (
            variant
            for variant in data.image_variants.get(template_name, [])
            if variant.format == image_format and variant.width == width
        ),
        None,
    )


def _recorded_widths(
    data: DailyDataFile, template: str | None, image_format: ImageFormat
) -> list[int]:
    """Return recorded widths for a template and exact image format."""
    template_name = template or next(iter(data.images), None)
    if template_name is None or data.image_variants is None:
        return []
    return [
        variant.width
        for variant in data.image_variants.get(template_name, [])
        if variant.format == image_format
    ]


async def _resolve_image_variant(
    data: DailyDataFile,
    config: AppConfig,
    template: str | None,
    image_format: ImageFormat | None,
    width: int | None,
    logger: logging.Logger,
) -> tuple[Path, ImageVariant] | JSONResponse:
    """Select and, when necessary, lazily create an image variant."""
    filename = _get_filename_for_template(data.images, template)
    if not filename:
        return JSONResponse(
            content=error_response(
                code=ErrorCode.API_TEMPLATE_NOT_FOUND,
                message=f"No image found for template: {template or 'default'}",
            ),
            status_code=status.HTTP_404_NOT_FOUND,
        )

    cache_dir = config.paths.cache_dir
    image_path, error = _get_image_path(filename, cache_dir, logger)
    if error is not None:
        return error
    assert image_path is not None

    render_config = config.get_templates_config().config
    selected_format: ImageFormat = image_format or "jpeg"
    if selected_format not in _configured_formats(render_config):
        return JSONResponse(
            content=error_response(
                code=ErrorCode.API_INVALID_PARAMETER,
                message=f"Requested image format is disabled: {selected_format}",
            ),
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    try:
        original_width = await asyncio.to_thread(image_width, image_path)
    except Exception as exc:
        logger.error("Failed to inspect source image %s: %s", filename, exc)
        return JSONResponse(
            content=error_response(
                code=ErrorCode.RENDER_SAVE_FAILED,
                message="Failed to inspect source image",
            ),
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    configured_widths = getattr(render_config, "output_widths", [])
    if not isinstance(configured_widths, list):
        configured_widths = []
    available_widths = configured_widths + _recorded_widths(
        data, template, selected_format
    )
    selected_width = _select_variant_width(width, original_width, available_widths)
    recorded = _recorded_variant(data, template, selected_format, selected_width)
    if recorded is not None:
        recorded_path, recorded_error = _get_image_path(recorded.filename, cache_dir, logger)
        if recorded_error is None:
            assert recorded_path is not None
            return recorded_path, recorded

    if selected_format == "jpeg" and selected_width == original_width:
        return image_path, ImageVariant(
            filename=filename, format="jpeg", width=original_width
        )

    try:
        generated = await asyncio.to_thread(
            ensure_variant,
            image_path,
            render_config,
            selected_format,
            None if selected_width == original_width else selected_width,
        )
    except Exception as exc:
        logger.warning("Lazy image variant generation failed for %s: %s", filename, exc)
        return JSONResponse(
            content=error_response(
                code=ErrorCode.GENERATION_FAILED,
                message="Image variant generation failed, please try again later",
            ),
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    variant_path, variant_error = _get_image_path(generated.filename, cache_dir, logger)
    if variant_error is not None:
        return variant_error
    assert variant_path is not None
    return variant_path, generated


def _handle_image_response(
    image_path: Path,
    variant: ImageVariant,
    cache_headers: dict[str, str],
    target_date: date,
    logger: logging.Logger,
) -> FileResponse:
    """Handle an already resolved image file response."""
    logger.info("Serving moyuren image for %s: %s", target_date, variant.filename)
    return FileResponse(
        path=image_path,
        media_type=f"image/{variant.format}",
        filename=variant.filename,
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
    encode: str = Query(
        "json",
        description="Output format: json, text, markdown, image, url",
        json_schema_extra={"enum": list(get_args(EncodeFormat))},
    ),
    template: str | None = Query(None, description="Template name, defaults to first available"),
    detail: bool = Query(False, description="Include detailed fields (only for encode=json)"),
    format: Annotated[
        ImageFormat | None,
        Query(description="Image format for encode=image or encode=url"),
    ] = None,
    width: Annotated[
        int | None,
        Query(gt=0, description="Requested image width for encode=image or encode=url"),
    ] = None,
) -> Response:
    """Get moyuren calendar image data or file.

    Unified endpoint supporting multiple output formats and query parameters.

    Query Parameters:
        - date: Target date (YYYY-MM-DD), defaults to today
        - encode: Output format (json/text/markdown/image/url), defaults to json
        - template: Template name, defaults to first available
        - detail: Include detailed fields (only for encode=json), defaults to false
        - format: Exact image format (jpeg/webp) for image and URL responses
        - width: Preferred image width for image and URL responses

    HTTP Caching:
        - History dates (< today): Cache-Control: immutable
        - Today's date: ETag + Last-Modified, supports 304 Not Modified

    Returns:
        - encode=json: JSON response with image metadata
        - encode=text: Plain text response
        - encode=markdown: Markdown formatted response
        - encode=image: JPEG or WebP image file
        - encode=url: Plain text URL for the selected image variant
    """
    logger = logging.getLogger(__name__)

    # Validate encode parameter
    valid_encodes = list(get_args(EncodeFormat))
    if encode not in valid_encodes:
        return JSONResponse(
            content=error_response(
                code=ErrorCode.API_INVALID_ENCODE,
                message=f"Invalid encode parameter: {encode}, must be one of {valid_encodes}",
            ),
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    if (format is not None or width is not None) and encode not in {"image", "url"}:
        return JSONResponse(
            content=error_response(
                code=ErrorCode.API_INVALID_PARAMETER,
                message="format and width are only valid for encode=image or encode=url",
            ),
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    config = request.app.state.config
    requested_format = format or "jpeg"
    if encode in {"image", "url"} and requested_format not in _configured_formats(
        config.get_templates_config().config
    ):
        return JSONResponse(
            content=error_response(
                code=ErrorCode.API_INVALID_PARAMETER,
                message=f"Requested image format is disabled: {requested_format}",
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
    data, error = await _load_data_for_date(request, target_date, logger, template)
    if error:
        return error
    assert data is not None

    base_domain = config.server.base_domain

    # Handle different output formats
    try:
        variant_path: Path | None = None
        variant: ImageVariant | None = None
        if encode in {"image", "url"}:
            resolved = await _resolve_image_variant(
                data, config, template, format, width, logger
            )
            if isinstance(resolved, JSONResponse):
                return resolved
            variant_path, variant = resolved

        identity = (
            f"{variant.format}:{variant.width}:{variant.filename}"
            if variant is not None
            else None
        )
        cache_headers = _build_cache_headers(target_date, data.updated_at, identity)
        today = today_business()
        if target_date == today and _check_not_modified(
            request,
            data.updated_at,
            identity,
            allow_if_modified_since=variant is None,
        ):
            return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=cache_headers)

        if encode == "image":
            assert variant_path is not None and variant is not None
            return _handle_image_response(
                variant_path, variant, cache_headers, target_date, logger
            )
        elif encode == "url":
            assert variant is not None
            return PlainTextResponse(
                _build_image_url(base_domain, variant.filename), headers=cache_headers
            )
        elif encode == "text":
            return _handle_text_response(data, base_domain, template, cache_headers, target_date, logger)
        elif encode == "markdown":
            return _handle_markdown_response(data, base_domain, template, cache_headers, target_date, logger)
        else:
            return _handle_json_response(data, base_domain, template, detail, cache_headers, target_date, logger)
    except StorageError as e:
        logger.error("Storage error: %s", e.message)
        return JSONResponse(
            content=error_response(code=e.code, message=e.message),
            status_code=get_http_status(e.code),
        )
