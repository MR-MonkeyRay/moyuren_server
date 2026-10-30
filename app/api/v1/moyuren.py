"""摸鱼日历图片 API 端点。"""

import asyncio
import logging
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Annotated, Literal, get_args

from fastapi import APIRouter, Query, Request, status
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response

from app.api.v1.common import build_image_url as _build_image_url
from app.api.v1.common import cache_etag as _cache_etag
from app.api.v1.common import check_not_modified as _check_not_modified
from app.core.config import AppConfig
from app.core.errors import ErrorCode, StorageError, error_response, get_http_status
from app.models.schemas import DailyDataFile, ErrorResponse, ImageVariant
from app.services.calendar import today_business
from app.services.generator import GenerationBusyError, generate_and_save_image
from app.services.variants import ensure_variant, image_width

router = APIRouter(prefix="/api/v1", tags=["moyuren"])
EncodeFormat = Literal["json", "text", "markdown", "image", "url"]
ImageFormat = Literal["jpeg", "webp"]


def _get_filename_for_template(images: dict[str, str], template: str | None) -> str | None:
    """从图片映射中获取指定模板的文件名。

    Args:
        images: 图片映射 {template_name: filename}
        template: 模板名，None 表示使用首个可用模板

    Returns:
        文件名，未找到时返回 None
    """
    if not images:
        return None

    if template is None:
        # 返回首张可用图片
        return next(iter(images.values()), None)

    # 返回指定模板的图片
    return images.get(template)


def _build_cache_headers(
    target_date: date, updated_at: int, identity: str | None = None
) -> dict[str, str]:
    """根据日期构建 HTTP 缓存头。

    Args:
        target_date: 数据的目标日期
        updated_at: 毫秒级更新时间戳

    Returns:
        缓存头字典
    """
    today = today_business()

    if target_date < today:
        # 历史数据不再变化
        return {
            "Cache-Control": "public, max-age=31536000, immutable",
        }
    else:
        # 当天数据使用 ETag 和 Last-Modified
        etag = _cache_etag(updated_at, identity)
        last_modified = datetime.fromtimestamp(updated_at / 1000, tz=UTC).strftime(
            "%a, %d %b %Y %H:%M:%S GMT"
        )
        return {
            "Cache-Control": "public, max-age=300, must-revalidate",
            "ETag": etag,
            "Last-Modified": last_modified,
        }


def _build_simple_response(
    data: DailyDataFile, base_domain: str, template: str | None
) -> dict[str, object]:
    """构建精简响应（encode=json，detail=false）。

    Returns:
        - date: YYYY-MM-DD
        - updated: YYYY/MM/DD HH:MM:SS
        - updated_at: 毫秒级时间戳
        - image: 完整 URL
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
    """构建公开的图片变体元数据，使用静态资源的绝对 URL。"""
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
    """构建详情响应（encode=json，detail=true）。

    仅返回公开详情字段及图片 URL，不暴露内部的 ``schema_version`` 元数据。
    """
    simple = _build_simple_response(data, base_domain, template)
    stored = data.model_dump(
        mode="json", exclude={"schema_version"}, exclude_unset=True
    )

    # 仅选择公开字段，不透传全部缓存数据
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
    """构建纯文本响应（encode=text）。

    格式：
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
    """构建 Markdown 响应（encode=markdown）。

    格式：
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
    """按需生成图片，并将生成错误转换为响应。"""
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
    """加载并校验指定日期的数据文件。

    Args:
        request: FastAPI 请求对象
        target_date: 要加载的目标日期
        logger: 日志记录器实例
        template: 请求的模板，None 表示使用首个可用模板
        allow_generation: 本次请求是否仍可尝试生成

    Returns:
        (data, error_response) 元组，成功时 error_response 为 None。
    """
    config = request.app.state.config
    cache_dir = Path(config.paths.cache_dir)
    data_file = cache_dir / "data" / f"{target_date.isoformat()}.json"

    # 当天的数据文件不存在时触发生成
    today = today_business()
    if target_date == today and not data_file.exists() and allow_generation:
        logger.info("Data file not found for today (%s), triggering generation...", target_date)
        error = await _generate_on_demand(request, logger)
        if error is not None:
            return None, error
        allow_generation = False

    # 读取数据文件
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
    """处理 JSON 格式响应（encode=json）。"""
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
    """处理纯文本格式响应（encode=text）。"""
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
    """处理 Markdown 格式响应（encode=markdown）。"""
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
    """解析 images 目录内已存在的图片文件路径。"""
    # 构建并校验图片路径
    images_dir = Path(cache_dir) / "images"

    # 校验文件名，防止路径穿越
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

    # 确保解析后的路径仍在 images_dir 内
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

    # 检查图片文件是否存在
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


def _select_variant_width(
    requested_width: int | None, original_width: int, configured_widths: list[int]
) -> int:
    """未指定宽度时返回原宽；否则选不小于请求值的最小宽度，无则选最大宽度。"""
    if requested_width is None:
        return original_width
    available = sorted({width for width in configured_widths if width < original_width} | {original_width})
    return next((width for width in available if width >= requested_width), available[-1])


def _recorded_variant(
    data: DailyDataFile, template: str | None, image_format: ImageFormat, width: int
) -> ImageVariant | None:
    """查找所选模板中精确匹配的已记录图片变体。"""
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
    """返回指定模板和图片格式的已记录宽度。"""
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
    """选择图片变体，必要时按需创建。"""
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
    if selected_format not in render_config.output_formats:
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
    available_widths = render_config.output_widths + _recorded_widths(
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
    """返回已解析的图片文件响应。"""
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
    date: str | None = Query(None, description="目标日期（YYYY-MM-DD），默认今天"),
    encode: str = Query(
        "json",
        description="输出格式：json、text、markdown、image、url",
        json_schema_extra={"enum": list(get_args(EncodeFormat))},
    ),
    template: str | None = Query(None, description="模板名，默认使用首个可用模板"),
    detail: bool = Query(False, description="包含详情字段（仅适用于 encode=json）"),
    format: Annotated[
        ImageFormat | None,
        Query(description="图片格式，适用于 encode=image 或 encode=url"),
    ] = None,
    width: Annotated[
        int | None,
        Query(gt=0, description="请求的图片宽度，适用于 encode=image 或 encode=url"),
    ] = None,
) -> Response:
    """获取摸鱼日历图片数据或文件。

    统一端点，支持多种输出格式和查询参数。

    查询参数：
        - date: 目标日期（YYYY-MM-DD），默认今天
        - encode: 输出格式（json/text/markdown/image/url），默认 json
        - template: 模板名，默认使用首个可用模板
        - detail: 包含详情字段（仅适用于 encode=json），默认 false
        - format: 图片和 URL 响应的指定图片格式（jpeg/webp）
        - width: 图片和 URL 响应的期望图片宽度

    HTTP 缓存：
        - 历史日期（早于今天）：Cache-Control: immutable
        - 当天日期：ETag + Last-Modified，支持 304 Not Modified

    Returns:
        - encode=json: 包含图片元数据的 JSON 响应
        - encode=text: 纯文本响应
        - encode=markdown: Markdown 格式响应
        - encode=image: JPEG 或 WebP 图片文件
        - encode=url: 所选图片变体的纯文本 URL
    """
    logger = logging.getLogger(__name__)

    # 校验 encode 参数
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
    if encode in {"image", "url"} and requested_format not in (
        config.get_templates_config().config.output_formats
    ):
        return JSONResponse(
            content=error_response(
                code=ErrorCode.API_INVALID_PARAMETER,
                message=f"Requested image format is disabled: {requested_format}",
            ),
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    # 解析并校验 date 参数
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

    # 加载目标日期的数据
    data, error = await _load_data_for_date(request, target_date, logger, template)
    if error:
        return error
    assert data is not None

    base_domain = config.server.base_domain

    # 处理不同的输出格式
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
