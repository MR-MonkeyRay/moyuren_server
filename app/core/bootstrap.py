"""Application service construction and startup helpers."""

import asyncio
import logging
import shutil
from collections.abc import Awaitable, Callable
from pathlib import Path

import httpx
from fastapi import FastAPI

from app.core.config import (
    AppConfig,
    CrazyThursdaySource,
    DailyEnglishSource,
    FunContentSource,
    GoldPriceSource,
    HolidaySource,
    NewsSource,
    StockIndexSource,
)
from app.core.errors import StorageError
from app.core.network import safe_exception_for_log
from app.core.scheduler import TaskScheduler
from app.core.services import AppServices
from app.models.schemas import DailyDataFile
from app.services.cache import CacheCleaner
from app.services.calendar import today_business
from app.services.compute import DataComputer, DomainDataAggregator, TemplateAdapter
from app.services.daily_english import CachedDailyEnglishService, build_dict_backend
from app.services.fetcher import CachedDataFetcher
from app.services.fun_content import CachedFunContentService
from app.services.generator import ImageGenerator, generate_and_save_image
from app.services.gold_price import CachedGoldPriceService
from app.services.holiday import CachedHolidayService
from app.services.kfc import CachedKfcService
from app.services.renderer import ImageRenderer
from app.services.stock_index import StockIndexService


def build_services(
    config: AppConfig, logger: logging.Logger, http_client: httpx.AsyncClient
) -> AppServices:
    """Build the application service container from configuration."""
    cache_dir = Path(config.paths.cache_dir)
    daily_cache_dir = cache_dir / "daily"

    news_source = config.get_source(NewsSource)
    data_fetcher = (
        CachedDataFetcher(
            source=news_source,
            logger=logger,
            cache_dir=daily_cache_dir,
            http_client=http_client,
            proxy_url=config.network.proxy_url,
            ghproxy_urls=config.network.ghproxy_urls,
            instances=config.instances,
        )
        if news_source
        else None
    )

    data_computer = DataComputer(DomainDataAggregator(), TemplateAdapter())
    holiday_source = config.get_source(HolidaySource)
    holiday_service = CachedHolidayService(
        logger=logger,
        cache_dir=daily_cache_dir,
        raw_cache_dir=cache_dir / "holidays",
        ghproxy_urls=config.network.ghproxy_urls,
        timeout_sec=holiday_source.timeout_sec if holiday_source else 10,
        proxy_url=config.network.proxy_url,
    )

    fun_content_source = config.get_source(FunContentSource)
    fun_content_service = (
        CachedFunContentService(
            config=fun_content_source,
            logger=logger,
            cache_dir=daily_cache_dir,
            proxy_url=config.network.proxy_url,
            instances=config.instances,
        )
        if fun_content_source
        else None
    )

    crazy_thursday_source = config.get_source(CrazyThursdaySource)
    kfc_service = (
        CachedKfcService(
            config=crazy_thursday_source,
            logger=logger,
            cache_dir=daily_cache_dir,
            proxy_url=config.network.proxy_url,
            instances=config.instances,
        )
        if crazy_thursday_source
        else None
    )

    stock_index_source = config.get_source(StockIndexSource)
    stock_index_service = (
        StockIndexService(stock_index_source, proxy_url=config.network.proxy_url)
        if stock_index_source
        else None
    )

    gold_price_source = config.get_source(GoldPriceSource)
    gold_price_service = (
        CachedGoldPriceService(
            config=gold_price_source,
            logger=logger,
            cache_dir=daily_cache_dir,
            http_client=http_client,
            proxy_url=config.network.proxy_url,
            instances=config.instances,
        )
        if gold_price_source
        else None
    )

    daily_english_service = None
    daily_english_source = config.get_source(DailyEnglishSource)
    if daily_english_source and daily_english_source.enabled:
        dict_backend = build_dict_backend(
            cfg=daily_english_source.backend,
            cache_dir=config.paths.cache_dir,
            ghproxy_urls=config.network.ghproxy_urls,
            logger=logger,
            proxy_url=config.network.proxy_url,
        )
        daily_english_service = CachedDailyEnglishService(
            config=daily_english_source,
            backend=dict_backend,
            logger=logger,
            cache_dir=daily_cache_dir,
            proxy_url=config.network.proxy_url,
        )

    image_renderer = ImageRenderer(
        templates_config=config.get_templates_config(),
        images_dir=str(cache_dir / "images"),
        render_config=config.templates.config,
        logger=logger,
        proxy_url=config.network.proxy_url,
    )
    cache_cleaner = CacheCleaner(
        cache_dir=str(cache_dir),
        retain_days=config.cache.retain_days,
        max_images_per_template_per_day=config.cache.max_images_per_template_per_day,
        logger=logger,
    )
    return AppServices(
        data_fetcher=data_fetcher,
        holiday_service=holiday_service,
        fun_content_service=fun_content_service,
        kfc_service=kfc_service,
        stock_index_service=stock_index_service,
        gold_price_service=gold_price_service,
        daily_english_service=daily_english_service,
        image_renderer=image_renderer,
        data_computer=data_computer,
        cache_cleaner=cache_cleaner,
        generator=ImageGenerator(),
    )


def register_scheduled_jobs(
    scheduler: TaskScheduler,
    config: AppConfig,
    job: Callable[[], Awaitable[None]],
    logger: logging.Logger,
) -> None:
    """Register configured image generation jobs on a scheduler."""
    if config.scheduler.mode == "hourly":
        scheduler.add_hourly_job(
            job_id="generate_moyuren_image",
            func=job,
            minute=config.scheduler.minute_of_hour,
        )
        logger.info(
            f"Scheduler mode: hourly (minute={config.scheduler.minute_of_hour:02d})"
        )
        return

    for idx, time_str in enumerate(config.scheduler.daily_times):
        try:
            hour, minute = map(int, time_str.split(":"))
            job_id = (
                f"generate_moyuren_image_{idx}"
                if len(config.scheduler.daily_times) > 1
                else "generate_moyuren_image"
            )
            scheduler.add_daily_job(
                job_id=job_id, func=job, hour=hour, minute=minute
            )
        except (ValueError, AttributeError) as exc:
            logger.warning(f"Invalid time format '{time_str}', skipping: {exc}")
    logger.info(f"Scheduler mode: daily (times={config.scheduler.daily_times})")


async def warmup_daily_cache(
    services: AppServices, config: AppConfig, logger: logging.Logger
) -> None:
    """Warm daily data source caches."""
    logger.info("Warming up daily cache...")
    named_tasks: list[tuple[str, Awaitable[object]]] = [
        ("holiday", services.holiday_service.get())
    ]
    optional_services = (
        ("data_fetcher", services.data_fetcher),
        ("fun_content", services.fun_content_service),
        ("kfc", services.kfc_service),
        ("gold_price", services.gold_price_service),
        ("daily_english", services.daily_english_service),
    )
    named_tasks.extend(
        (name, service.get())
        for name, service in optional_services
        if service is not None
    )
    results = await asyncio.gather(
        *(coroutine for _, coroutine in named_tasks), return_exceptions=True
    )
    for (name, _), result in zip(named_tasks, results):
        if isinstance(result, Exception):
            logger.warning(
                "Cache warmup [%s] failed: %s",
                name,
                safe_exception_for_log(result, config.network.proxy_url),
            )
    logger.info("Daily cache warmup completed")


async def ensure_today_data(app: FastAPI) -> None:
    """Validate today's cached data and generate an initial image if needed."""
    config: AppConfig = app.state.config
    logger: logging.Logger = app.state.logger
    today_str = today_business().isoformat()
    data_file = (
        Path(config.paths.cache_dir)
        / "data"
        / f"{today_str}.json"
    )
    need_regenerate, cache_is_valid_but_expired = _inspect_today_data(
        data_file, today_str, logger
    )
    if not need_regenerate:
        return

    if cache_is_valid_but_expired:
        logger.info("Cache expired but valid, triggering background refresh...")
        try:
            await generate_and_save_image(app)
            logger.info("Background refresh completed successfully")
        except asyncio.CancelledError:
            logger.info("Background refresh cancelled due to shutdown")
            raise
        except Exception as exc:
            logger.error(
                "Background refresh failed: %s",
                safe_exception_for_log(exc, config.network.proxy_url),
            )
            logger.warning("Stale cache will be used until next scheduled refresh")
        return

    if data_file.exists():
        logger.info("Removing invalid data file...")
        try:
            if data_file.is_file():
                data_file.unlink()
            elif data_file.is_dir():
                logger.error(f"Data file path is a directory, not a file: {data_file}")
                shutil.rmtree(data_file)
        except (OSError, IsADirectoryError) as exc:
            logger.error(f"Failed to remove invalid data file: {exc}")

    logger.info("No valid cache found, generating initial image...")
    try:
        await generate_and_save_image(app)
        logger.info("Initial image generated successfully")
    except Exception as exc:
        logger.error(
            "Initial image generation failed: %s",
            safe_exception_for_log(exc, config.network.proxy_url),
        )
        logger.warning(
            "Service will start without cache, API will trigger on-demand generation"
        )


def _inspect_today_data(
    data_file: Path, today_str: str, logger: logging.Logger
) -> tuple[bool, bool]:
    """Return whether cached data needs regeneration and whether it is stale only."""
    if not data_file.exists():
        logger.info("No existing data file found for today")
        return True, False
    try:
        data = DailyDataFile.load(data_file)
        cached_date = data.date
        if cached_date != today_str:
            logger.info(f"Cache date mismatch: {cached_date} != {today_str}")
            return True, True
    except StorageError as exc:
        logger.warning(f"Data file invalid or unreadable: {exc}")
        return True, False
    return False, False
