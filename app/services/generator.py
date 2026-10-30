"""Image generation service module."""

import asyncio
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from app.core.errors import GenerationBusyError, StorageError
from app.core.filelock import FileLockTimeout, async_file_lock
from app.core.metrics import (
    GenerationResult,
    record_generation_duration,
    record_render_duration,
)
from app.core.network import safe_exception_for_log
from app.core.services import AppServices
from app.models.schemas import DailyDataFile
from app.services.cache import CacheCleaner
from app.services.calendar import get_display_timezone, now_business
from app.services.daily_cache import DailyCache

INFLIGHT_WAIT_TIMEOUT_SEC = 90

# Keep background cleanup alive until its completion callback releases it.
_cleanup_tasks: set[asyncio.Task[None]] = set()


def _read_data_file(data_file: Path) -> dict | None:
    """读取 data 文件内容"""
    try:
        return DailyDataFile.load(data_file).model_dump(mode="json", exclude_unset=True)
    except StorageError:
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


def _cached_image_exists(images_dir: Path, filename: str) -> bool:
    """检查缓存图片文件是否存在于图片目录中"""
    if "/" in filename or "\\" in filename or ".." in filename:
        return False

    image_path = images_dir / filename
    try:
        return image_path.resolve().parent == images_dir.resolve() and image_path.is_file()
    except (OSError, ValueError):
        return False


def _read_latest_filename(data_file: Path, template_name: str | None = None) -> str | None:
    """读取 data 文件中的最新文件名

    Args:
        data_file: Path to data file.
        template_name: Optional template name to look up.

    Returns:
        文件名，如果读取失败则返回 None
    """
    try:
        images = DailyDataFile.load(data_file).images
        if not template_name:
            return next(iter(images.values()), None)
        return images.get(template_name)
    except StorageError:
        return None


async def _fetch_all_data_parallel(app: FastAPI, logger: logging.Logger) -> dict[str, Any]:
    """Fetch all data sources in parallel.

    Uses asyncio.gather to fetch data from multiple sources concurrently,
    significantly reducing total fetch time compared to sequential fetching.

    Args:
        app: FastAPI application instance with services in app.state.
        logger: Logger instance for logging.

    Returns:
        Dictionary containing all fetched data with appropriate defaults for failures.
    """
    services: AppServices = app.state.services
    data_fetcher = services.data_fetcher
    holiday_service = services.holiday_service
    fun_content_service = services.fun_content_service
    kfc_service = services.kfc_service
    stock_index_service = services.stock_index_service
    gold_price_service = services.gold_price_service
    daily_english_service = services.daily_english_service
    stale_sources: list[str] = []

    async def get_cached_data(service: DailyCache[Any]) -> Any:
        """Capture fallback status before another caller can update it."""
        data = await service.get()
        if data is not None and getattr(service, "last_outcome", None) == "stale":
            stale_sources.append(service.namespace)
        return data

    # Define fetch tasks
    async def fetch_api_data() -> dict[str, Any]:
        """获取基础 API 数据。

        Returns:
            API 原始数据字典；服务不存在、返回空值或请求失败时返回空字典。

        Side Effects:
            捕获异常并写入警告日志。
        """
        if not data_fetcher:
            return {}
        try:
            data = await get_cached_data(data_fetcher)
            return data if data is not None else {}
        except Exception as e:
            logger.warning("Failed to fetch API data: %s", safe_exception_for_log(e))
            return {}

    async def fetch_holidays() -> list[Any]:
        """获取节假日数据。

        Returns:
            节假日列表；服务不存在、获取失败或返回空值时返回空列表。

        Side Effects:
            捕获异常并写入警告日志。
        """
        if not holiday_service:
            return []
        try:
            holidays = await get_cached_data(holiday_service)
            return holidays if holidays is not None else []
        except Exception as e:
            logger.warning("Failed to fetch holidays: %s", safe_exception_for_log(e))
            return []

    async def fetch_fun_content() -> dict[str, Any] | None:
        """获取趣味内容。

        Returns:
            趣味内容数据；服务不存在或获取失败时返回 None。

        Side Effects:
            捕获异常并写入警告日志。
        """
        if not fun_content_service:
            return None
        try:
            return await get_cached_data(fun_content_service)
        except Exception as e:
            logger.warning("Failed to fetch fun content: %s", safe_exception_for_log(e))
            return None

    async def fetch_kfc() -> str | None:
        """获取疯狂星期四文案。

        Returns:
            KFC 文案数据；服务不存在或获取失败时返回 None。

        Side Effects:
            捕获异常并写入警告日志。
        """
        if not kfc_service:
            return None
        try:
            return await get_cached_data(kfc_service)
        except Exception as e:
            logger.warning("Failed to fetch KFC content: %s", safe_exception_for_log(e))
            return None

    async def fetch_stock_indices() -> dict[str, Any] | None:
        """获取股票指数数据。

        Returns:
            股票指数数据；服务不存在或获取失败时返回 None。

        Side Effects:
            捕获异常并写入警告日志。
        """
        if not stock_index_service:
            return None
        try:
            data = await stock_index_service.fetch_indices()
            # 失败占位数据同样带 is_stale，但已在模板中显示"数据更新失败"，不算缓存降级
            if data.get("is_stale") and not data.get("is_data_missing"):
                stale_sources.append("stock_index")
            return data
        except Exception as e:
            logger.warning("Failed to fetch stock indices: %s", safe_exception_for_log(e))
            return None

    async def fetch_gold_price() -> dict[str, Any] | None:
        """获取金价数据。

        Returns:
            金价数据；服务不存在或获取失败时返回 None。

        Side Effects:
            捕获异常并写入警告日志。
        """
        if not gold_price_service:
            return None
        try:
            return await get_cached_data(gold_price_service)
        except Exception as e:
            logger.warning("Failed to fetch gold price: %s", safe_exception_for_log(e))
            return None

    async def fetch_daily_english() -> dict[str, Any] | None:
        """获取每日英语内容。

        Returns:
            每日英语数据；服务不存在或获取失败时返回 None。

        Side Effects:
            捕获异常并写入警告日志。
        """
        if not daily_english_service:
            return None
        try:
            return await get_cached_data(daily_english_service)
        except Exception as e:
            logger.warning("Failed to fetch daily english: %s", safe_exception_for_log(e))
            return None

    # Execute all fetches in parallel
    (
        api_data_result,
        holidays_result,
        fun_content_result,
        kfc_result,
        stock_indices_result,
        gold_price_result,
        daily_english_result,
    ) = await asyncio.gather(
        fetch_api_data(),
        fetch_holidays(),
        fetch_fun_content(),
        fetch_kfc(),
        fetch_stock_indices(),
        fetch_gold_price(),
        fetch_daily_english(),
    )

    # Merge results into raw_data with type safety
    # Ensure raw_data is a dict (DailyCache.get() might return non-dict if corrupted)
    if not isinstance(api_data_result, dict):
        logger.warning(
            "raw_data is not dict (got %s), using empty dict", type(api_data_result).__name__
        )
        raw_data = {}
    else:
        raw_data = dict(api_data_result)
    raw_data["holidays"] = holidays_result
    raw_data["fun_content"] = fun_content_result
    raw_data["kfc_copy"] = kfc_result
    raw_data["stock_indices"] = stock_indices_result
    raw_data["gold_price"] = gold_price_result
    raw_data["daily_english"] = daily_english_result
    raw_data["stale_sources"] = sorted(set(stale_sources))

    # Log fetch results (count actual API data keys, excluding merged fields)
    api_keys = [k for k in raw_data if k not in ("holidays", "fun_content", "kfc_copy", "stock_indices", "gold_price", "daily_english", "stale_sources")]
    logger.info("Fetched data: %d API endpoints, parallel fetch completed", len(api_keys))
    if holidays_result:
        logger.info("Fetched %d holidays", len(holidays_result))
    if fun_content_result and isinstance(fun_content_result, dict):
        logger.info("Fetched fun content: %s", fun_content_result.get("title"))
    if kfc_result:
        logger.info("Fetched KFC Crazy Thursday content")
    if stock_indices_result and isinstance(stock_indices_result, dict):
        logger.info("Fetched %d stock indices", len(stock_indices_result.get("items", [])))
    if gold_price_result and isinstance(gold_price_result, dict):
        logger.info("Fetched gold price: %s", gold_price_result.get("today_price"))
    if daily_english_result and isinstance(daily_english_result, dict):
        logger.info("Fetched daily english: %s", daily_english_result.get("word"))

    return raw_data


def _schedule_cache_cleanup(cache_cleaner: CacheCleaner, logger: logging.Logger) -> None:
    """Schedule cache cleanup as a fire-and-forget background task.

    This function creates a background task for cache cleanup that doesn't
    block the main generation flow. Errors are logged but don't affect
    the caller.

    Args:
        cache_cleaner: CacheCleaner instance.
        logger: Logger instance for logging.
    """

    async def _cleanup_task() -> None:
        """在线程中执行缓存清理并记录结果。

        Side Effects:
            删除过期缓存文件，并在删除成功或失败时写入日志。
        """
        try:
            result: dict[str, Any] = await asyncio.to_thread(cache_cleaner.cleanup)
            if result.get("deleted_files", 0) > 0:
                logger.info(
                    "Cleaned up %d expired cache file(s), freed %.1f KB",
                    result["deleted_files"],
                    result["freed_bytes"] / 1024,
                )
        except Exception as e:
            logger.warning("Cache cleanup failed: %s", e)

    task = asyncio.create_task(_cleanup_task())
    _cleanup_tasks.add(task)
    task.add_done_callback(_cleanup_tasks.discard)


class ImageGenerator:
    """应用内共享图片生成任务，避免重复获取数据和渲染。"""

    def __init__(self) -> None:
        """初始化当前任务及其覆盖的模板集合。"""
        self._task: asyncio.Task[dict[str, str]] | None = None
        self._templates: frozenset[str] = frozenset()

    async def generate(
        self, app: FastAPI, template_name: str | None = None
    ) -> dict[str, str]:
        """共享覆盖所需模板的任务，或等待后生成尚未覆盖的模板。"""
        deadline = asyncio.get_running_loop().time() + INFLIGHT_WAIT_TIMEOUT_SEC
        while self._task is not None and not self._task.done():
            task = self._task
            covered = template_name is None or template_name in self._templates
            wait_timeout = asyncio.timeout_at(deadline)
            try:
                async with wait_timeout:
                    result = await asyncio.shield(task)
            except TimeoutError as exc:
                if not wait_timeout.expired():
                    raise
                app.state.logger.info("Image generation wait timed out (in-process)")
                raise GenerationBusyError("Image generation wait timed out") from exc
            if covered:
                return result

        templates_config = app.state.config.get_templates_config()
        self._templates = frozenset(
            [template_name] if template_name else (item.name for item in templates_config.items)
        )
        task = asyncio.create_task(self._generate_and_save_image(app, template_name))
        self._task = task
        task.add_done_callback(self._generation_done)
        return await asyncio.shield(task)

    def _generation_done(self, task: asyncio.Task[dict[str, str]]) -> None:
        """释放已完成的任务并接收取消调用方后遗留的异常。"""
        if self._task is task:
            self._task = None
            self._templates = frozenset()
        if not task.cancelled():
            task.exception()

    async def shutdown(self) -> None:
        """关闭依赖服务前取消并等待当前生成任务。"""
        task = self._task
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def _generate_and_save_image(
        self, app: FastAPI, template_name: str | None = None
    ) -> dict[str, str]:
        """Record one duration for each shared generation run."""
        started = time.monotonic()
        result: GenerationResult = "failure"
        try:
            images = await self._run_generation(app, template_name)
            templates = app.state.config.get_templates_config()
            expected = {template_name} if template_name else {item.name for item in templates.items}
            result = "success" if expected.issubset(images) else "partial"
            return images
        finally:
            record_generation_duration(time.monotonic() - started, result)

    async def _run_generation(
        self, app: FastAPI, template_name: str | None = None
    ) -> dict[str, str]:
        """Generate images and update data file.

        This function performs the complete image generation pipeline:
        1. Fetch data from API endpoints
        2. Compute template context
        3. Render HTML to image for each template
        4. Update cache/data/{date}.json atomically

        When template_name is None, all configured templates are rendered.
        When template_name is specified, only that template is rendered.

        Args:
            app: FastAPI application instance with services in app.state.
            template_name: Optional template name. None renders all templates.

        Returns:
            Dict mapping template names to generated image filenames.
            Templates that failed to render will be omitted from the dict.

        Raises:
            StorageError: If no templates are configured or all templates fail to render.
            GenerationBusyError: If generation is locked by another process.
        """
        services: AppServices = app.state.services
        logger = app.state.logger
        config = app.state.config
        cache_dir = Path(config.paths.cache_dir)
        data_dir = cache_dir / "data"
        images_dir = cache_dir / "images"

        # Determine which templates to render
        templates_config = config.get_templates_config()
        if template_name:
            templates_to_render = [templates_config.get_template(template_name)]
        else:
            templates_to_render = list(templates_config.items)

        if not templates_to_render:
            raise StorageError("No templates configured for rendering")

        lock_file = cache_dir / ".generation.lock"
        try:
            async with async_file_lock(lock_file, timeout=5.0):
                now = now_business()
                business_date = now.date()
                pre_cached: dict[str, str] = {}
                # 二次检查：获取锁后检查是否已有新生成的文件
                today_str = business_date.isoformat()
                data_file = data_dir / f"{today_str}.json"
                if data_file.exists():
                    data = _read_data_file(data_file)
                    if data and _is_recently_updated(data, threshold_sec=10):
                        cached_results: dict[str, str] = {}
                        missing_templates = []
                        for t in templates_to_render:
                            fn = _read_latest_filename(data_file, t.name)
                            if fn and _cached_image_exists(images_dir, fn):
                                cached_results[t.name] = fn
                            else:
                                missing_templates.append(t)
                        if not missing_templates:
                            logger.info(
                                "All %d template(s) recently updated, skipping generation",
                                len(templates_to_render),
                            )
                            return cached_results
                        # Only render missing templates, carry over cached results
                        templates_to_render = missing_templates
                        pre_cached = cached_results
                        logger.info(
                            "Found %d cached, %d to render",
                            len(cached_results),
                            len(missing_templates),
                        )

                logger.info("Starting image generation for %d template(s)...", len(templates_to_render))

                # 1. Fetch all data sources in parallel (使用缓存服务)
                raw_data = await _fetch_all_data_parallel(app, logger)

                # 2. Compute template context (once for all templates)
                template_data = services.data_computer.compute(raw_data, now=now)
                logger.info("Template data computed")

                # 3. Render each template and update data file
                # TODO: Consider using asyncio.gather for parallel rendering when template count grows
                results: dict[str, str] = dict(pre_cached)
                failed_templates: list[str] = []
                for template_item in templates_to_render:
                    tname = template_item.name
                    try:
                        t_start = time.monotonic()
                        try:
                            filename = await services.image_renderer.render(
                                template_data,
                                template_name=tname,
                                now=now,
                            )
                        finally:
                            record_render_duration(tname, time.monotonic() - t_start)
                        t_elapsed = time.monotonic() - t_start
                        logger.info("Image rendered for '%s': %s (%.1fs)", tname, filename, t_elapsed)

                        await _update_data_file(
                            data_dir=str(data_dir),
                            filename=filename,
                            template_data=template_data,
                            raw_data=raw_data,
                            config=config,
                            template_name=tname,
                            now=now,
                        )
                        results[tname] = filename
                    except Exception:
                        logger.exception("Failed to render template '%s'", tname)
                        failed_templates.append(tname)

                if not results:
                    raise StorageError("All templates failed to render")

                if failed_templates:
                    logger.warning(
                        "Partial render failure: %d template(s) failed: %s",
                        len(failed_templates),
                        ", ".join(failed_templates),
                    )

                # 4. Clean up old cache (fire-and-forget, non-blocking)
                _schedule_cache_cleanup(services.cache_cleaner, logger)

                return results
        except FileLockTimeout as exc:
            logger.warning("Image generation skipped: another process is generating")
            raise GenerationBusyError("Image generation locked by another process") from exc


async def generate_and_save_image(
    app: FastAPI, template_name: str | None = None
) -> dict[str, str]:
    """通过应用服务容器生成图片或等待已有任务。"""
    services: AppServices = app.state.services
    return await services.generator.generate(app, template_name)


async def _update_data_file(
    data_dir: str,
    filename: str,
    template_data: dict,
    raw_data: dict,
    config: Any,
    template_name: str,
    now: datetime | None = None,
) -> None:
    """Atomically update the daily data file with latest image information.

    Args:
        data_dir: Path to the data directory (e.g., "cache/data").
        filename: Generated image filename.
        template_data: Template data dictionary from DataComputer.
        raw_data: Raw data dictionary containing original API responses.
        config: Application config.
        template_name: Template name used for rendering.
        now: Optional business-time snapshot for this generation.

    Raises:
        StorageError: If file write fails.
    """

    if now is None:
        now = now_business()
    business_date = now.date()

    def _write_data() -> None:
        """同步构建并原子写入业务日期的数据文件。

        Raises:
            StorageError: 当临时文件写入或替换目标数据文件失败时抛出。

        Side Effects:
            创建数据目录、合并已有图片映射，并替换当天 JSON 数据文件。
        """
        dir_path = Path(data_dir)
        dir_path.mkdir(parents=True, exist_ok=True)

        data_file = dir_path / f"{business_date.isoformat()}.json"

        # Extract data from template_data
        date_info = template_data.get("date", {})
        fun_content_raw = template_data.get("history", {})
        kfc_content_raw = template_data.get("kfc_content")

        # Build fun_content
        fun_content = None
        if fun_content_raw:
            title = fun_content_raw.get("title", "")
            content_type = fun_content_raw.get("type")
            if content_type is None:
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

        # Cache freshness tracks write time; business fields keep the snapshot.
        written_at = datetime.now(get_display_timezone())
        updated_str = written_at.strftime("%Y/%m/%d %H:%M:%S")
        updated_at_ms = int(written_at.timestamp() * 1000)

        # Read existing data file to merge images mapping
        existing_images: dict[str, str] = {}
        if data_file.exists():
            try:
                existing_images.update(DailyDataFile.load(data_file).images)
            except StorageError:
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
            "gold_price": raw_data.get("gold_price"),
            "daily_english": template_data.get("daily_english"),
            "is_stale": template_data.get("data_freshness", {}).get("is_stale", False),
            "source_date": template_data.get("data_freshness", {}).get("source_date"),
            "stale_sources": template_data.get("data_freshness", {}).get("stale_sources", []),
        }

        DailyDataFile.model_validate(data).save_atomic(data_file)

    await asyncio.to_thread(_write_data)
