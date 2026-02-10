"""Image generation service module."""

import asyncio
import json
import os
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from filelock import FileLock, Timeout

from app.core.errors import StorageError
from app.services.calendar import get_display_timezone, today_business


class GenerationBusyError(Exception):
    """生成任务正在进行中"""

    pass


# 进程内锁
_async_lock: asyncio.Lock | None = None


def _get_async_lock() -> asyncio.Lock:
    """获取或创建异步锁（单例模式）"""
    global _async_lock
    if _async_lock is None:
        _async_lock = asyncio.Lock()
    return _async_lock


def _read_data_file(data_file: Path) -> dict | None:
    """读取 data 文件内容"""
    try:
        with data_file.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _is_recently_updated(state_data: dict, threshold_sec: int = 10) -> bool:
    """检查 state 是否在阈值时间内更新"""
    updated_at = state_data.get("updated_at")
    if not isinstance(updated_at, (int, float)) or isinstance(updated_at, bool):
        return False
    if updated_at <= 0:
        return False
    updated_time = updated_at / 1000
    return time.time() - updated_time < threshold_sec


def _read_latest_filename(data_file: Path, template_name: str | None = None) -> str | None:
    """读取 data 文件中的最新文件名

    Args:
        data_file: Path to data file.
        template_name: Optional template name to look up.

    Returns:
        文件名，如果读取失败则返回 None
    """
    try:
        with data_file.open("r", encoding="utf-8") as f:
            data = json.load(f)
            if not isinstance(data, dict):
                return None

            # Read from images mapping
            images = data.get("images")
            if not isinstance(images, dict):
                return None

            # If template_name not specified, use first available
            if not template_name:
                return next(iter(images.values()), None)

            return images.get(template_name)
    except (FileNotFoundError, json.JSONDecodeError, KeyError, OSError):
        return None


async def _fetch_all_data_parallel(app: FastAPI, logger) -> dict:
    """Fetch all data sources in parallel.

    Uses asyncio.gather to fetch data from multiple sources concurrently,
    significantly reducing total fetch time compared to sequential fetching.

    Args:
        app: FastAPI application instance with services in app.state.
        logger: Logger instance for logging.

    Returns:
        Dictionary containing all fetched data with appropriate defaults for failures.
    """
    # 优先使用服务容器，兼容旧方式
    services = getattr(app.state, "services", None)
    data_fetcher = services.data_fetcher if services else app.state.data_fetcher
    holiday_service = services.holiday_service if services else app.state.holiday_service
    fun_content_service = services.fun_content_service if services else app.state.fun_content_service
    kfc_service = services.kfc_service if services else app.state.kfc_service
    stock_index_service = services.stock_index_service if services else app.state.stock_index_service

    # Define fetch tasks
    async def fetch_api_data():
        try:
            data = await data_fetcher.get()
            return data if data is not None else {}
        except Exception as e:
            logger.warning(f"Failed to fetch API data: {e}")
            return {}

    async def fetch_holidays():
        try:
            holidays = await holiday_service.get()
            return holidays if holidays is not None else []
        except Exception as e:
            logger.warning(f"Failed to fetch holidays: {e}")
            return []

    async def fetch_fun_content():
        try:
            return await fun_content_service.get()
        except Exception as e:
            logger.warning(f"Failed to fetch fun content: {e}")
            return None

    async def fetch_kfc():
        if not kfc_service:
            return None
        try:
            return await kfc_service.get()
        except Exception as e:
            logger.warning(f"Failed to fetch KFC content: {e}")
            return None

    async def fetch_stock_indices():
        if not stock_index_service:
            return None
        try:
            return await stock_index_service.fetch_indices()
        except Exception as e:
            logger.warning(f"Failed to fetch stock indices: {e}")
            return None

    # Execute all fetches in parallel
    results = await asyncio.gather(
        fetch_api_data(),
        fetch_holidays(),
        fetch_fun_content(),
        fetch_kfc(),
        fetch_stock_indices(),
    )

    # Merge results into raw_data with type safety
    # Ensure raw_data is a dict (DailyCache.get() might return non-dict if corrupted)
    if not isinstance(results[0], dict):
        logger.warning(f"raw_data is not dict (got {type(results[0]).__name__}), using empty dict")
        raw_data = {}
    else:
        raw_data = results[0]
    raw_data["holidays"] = results[1]
    raw_data["fun_content"] = results[2]
    raw_data["kfc_copy"] = results[3]
    raw_data["stock_indices"] = results[4]

    # Log fetch results (count actual API data keys, excluding merged fields)
    api_keys = [k for k in raw_data if k not in ("holidays", "fun_content", "kfc_copy", "stock_indices")]
    logger.info(f"Fetched data: {len(api_keys)} API endpoints, parallel fetch completed")
    if results[1]:
        logger.info(f"Fetched {len(results[1])} holidays")
    if results[2] and isinstance(results[2], dict):
        logger.info(f"Fetched fun content: {results[2].get('title')}")
    if results[3]:
        logger.info("Fetched KFC Crazy Thursday content")
    if results[4] and isinstance(results[4], dict):
        logger.info(f"Fetched {len(results[4].get('items', []))} stock indices")

    return raw_data


def _schedule_cache_cleanup(cache_cleaner, logger) -> None:
    """Schedule cache cleanup as a fire-and-forget background task.

    This function creates a background task for cache cleanup that doesn't
    block the main generation flow. Errors are logged but don't affect
    the caller.

    Args:
        cache_cleaner: CacheCleaner instance.
        logger: Logger instance for logging.
    """

    async def _cleanup_task():
        try:
            result = await asyncio.to_thread(cache_cleaner.cleanup)
            if result.get("deleted_files", 0) > 0:
                logger.info(
                    f"Cleaned up {result['deleted_files']} expired cache file(s), "
                    f"freed {result['freed_bytes'] / 1024:.1f} KB"
                )
        except Exception as e:
            logger.warning(f"Cache cleanup failed: {e}")

    asyncio.create_task(_cleanup_task())


async def generate_and_save_image(app: FastAPI, template_name: str | None = None) -> str:
    """Generate image and update data file.

    This function performs the complete image generation pipeline:
    1. Fetch data from API endpoints
    2. Compute template context
    3. Render HTML to image
    4. Update cache/data/{date}.json atomically

    Args:
        app: FastAPI application instance with services in app.state.
        template_name: Optional template name to use for rendering.

    Returns:
        Generated image filename.

    Raises:
        RenderError: If image rendering fails.
        StorageError: If state file update fails.
        GenerationBusyError: If generation is locked by another process.
    """
    logger = app.state.logger
    config = app.state.config
    cache_dir = Path(config.paths.cache_dir)
    data_dir = cache_dir / "data"

    # Resolve template name
    templates_config = config.get_templates_config()
    template_item = templates_config.get_template(template_name)
    resolved_template_name = template_item.name

    # 获取进程内锁
    async_lock = _get_async_lock()

    # 获取文件锁路径
    lock_file = cache_dir / ".generation.lock"
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    file_lock = FileLock(str(lock_file), timeout=5)

    # 快速获取进程内锁（避免请求排队）
    acquired = False
    try:
        await asyncio.wait_for(async_lock.acquire(), timeout=0.1)
        acquired = True
    except TimeoutError as exc:
        logger.info("Image generation skipped: another request is generating (in-process)")
        raise GenerationBusyError("Image generation already in progress") from exc

    try:
        try:
            await asyncio.to_thread(file_lock.acquire)
            try:
                # 二次检查：获取锁后检查是否已有新生成的文件
                today_str = today_business().isoformat()
                data_file = data_dir / f"{today_str}.json"
                if data_file.exists():
                    data = _read_data_file(data_file)
                    if data and _is_recently_updated(data, threshold_sec=10):
                        filename = _read_latest_filename(
                            data_file,
                            resolved_template_name,
                        )
                        if filename:
                            logger.info(
                                f"Data file recently updated for template '{resolved_template_name}', skipping generation"
                            )
                            return filename
                        logger.warning("Data file exists but unreadable, proceeding with generation")

                logger.info("Starting image generation...")

                # 1. Fetch all data sources in parallel (使用缓存服务)
                raw_data = await _fetch_all_data_parallel(app, logger)

                # 2. Compute template context
                template_data = app.state.data_computer.compute(raw_data)
                logger.info("Template data computed")

                # 3. Render image
                filename = await app.state.image_renderer.render(
                    template_data,
                    template_name=resolved_template_name,
                )
                logger.info(f"Image rendered: {filename}")

                # 4. Update cache/data/{date}.json atomically
                await _update_data_file(
                    data_dir=str(data_dir),
                    filename=filename,
                    template_data=template_data,
                    raw_data=raw_data,
                    config=config,
                    template_name=resolved_template_name,
                )

                # 5. Clean up old cache (fire-and-forget, non-blocking)
                _schedule_cache_cleanup(app.state.cache_cleaner, logger)

                return filename
            finally:
                try:
                    await asyncio.to_thread(file_lock.release)
                except Exception as e:
                    logger.warning(f"Failed to release file lock: {e}")
        except Timeout as exc:
            logger.warning("Image generation skipped: another process is generating")
            raise GenerationBusyError("Image generation locked by another process") from exc
    finally:
        if acquired:
            async_lock.release()



async def _update_data_file(
    data_dir: str,
    filename: str,
    template_data: dict,
    raw_data: dict,
    config: Any,
    template_name: str,
) -> None:
    """Atomically update the daily data file with latest image information.

    Args:
        data_dir: Path to the data directory (e.g., "cache/data").
        filename: Generated image filename.
        template_data: Template data dictionary from DataComputer.
        raw_data: Raw data dictionary containing original API responses.
        config: Application config.
        template_name: Template name used for rendering.

    Raises:
        StorageError: If file write fails.
    """

    def _write_data():
        dir_path = Path(data_dir)
        dir_path.mkdir(parents=True, exist_ok=True)

        now = datetime.now(get_display_timezone())
        business_date = today_business()
        data_file = dir_path / f"{business_date.isoformat()}.json"

        # Extract data from template_data
        date_info = template_data.get("date", {})
        fun_content_raw = template_data.get("history", {})
        kfc_content_raw = template_data.get("kfc_content")

        # Build fun_content
        fun_content = None
        if fun_content_raw:
            title = fun_content_raw.get("title", "")
            content_type = "unknown"
            if "冷笑话" in title:
                content_type = "dad_joke"
            elif "一言" in title:
                content_type = "hitokoto"
            elif "段子" in title:
                content_type = "duanzi"
            elif "摸鱼" in title:
                content_type = "moyu_quote"
            fun_content = {"type": content_type, "title": title, "text": fun_content_raw.get("content", "")}

        # Build KFC content
        kfc_content = None
        if kfc_content_raw and isinstance(kfc_content_raw, dict):
            kfc_content = kfc_content_raw.get("content")

        # Timestamps
        updated_str = now.strftime("%Y/%m/%d %H:%M:%S")
        updated_at_ms = int(now.timestamp() * 1000)

        # Read existing data file to merge images mapping
        existing_images: dict[str, str] = {}
        if data_file.exists():
            try:
                with data_file.open("r", encoding="utf-8") as f:
                    existing_data = json.load(f)
                if isinstance(existing_data, dict) and isinstance(existing_data.get("images"), dict):
                    existing_images.update(existing_data["images"])
            except (OSError, json.JSONDecodeError):
                pass

        # Update images mapping
        existing_images[template_name] = filename

        # Build data file content
        data = {
            "date": business_date.isoformat(),
            "updated": updated_str,
            "updated_at": updated_at_ms,
            "images": existing_images,
            "weekday": date_info.get("week_cn", ""),
            "lunar_date": date_info.get("lunar_date", ""),
            "is_crazy_thursday": now.weekday() == 3,
            "date_info": template_data.get("date"),
            "weekend": template_data.get("weekend"),
            "solar_term": template_data.get("solar_term"),
            "guide": template_data.get("guide"),
            "holidays": [
                {k: v for k, v in h.items() if k != "color"}
                for h in template_data.get("holidays", [])
                if isinstance(h, dict)
            ],
            "news_list": [
                item.get("text", "") for item in template_data.get("news_list", []) if isinstance(item, dict)
            ],
            "news_meta": template_data.get("news_meta"),
            "fun_content": fun_content,
            "kfc_content": kfc_content,
            "kfc_content_full": template_data.get("kfc_content"),
            "stock_indices": raw_data.get("stock_indices"),
        }

        # Atomic write
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                dir=dir_path,
                prefix=".data_",
                suffix=".tmp",
                delete=False,
            ) as tmp_file:
                json.dump(data, tmp_file, ensure_ascii=False, indent=2)
                tmp_path = tmp_file.name
            os.replace(tmp_path, data_file)
        except Exception as e:
            if tmp_path and Path(tmp_path).exists():
                Path(tmp_path).unlink(missing_ok=True)
            raise StorageError(
                message=f"Failed to update data file {data_file}: {e}",
            ) from e

    await asyncio.to_thread(_write_data)
