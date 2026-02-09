"""Moyuren API application entry point."""

import asyncio
import contextlib
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.api.v1.moyuren import router as moyuren_router
from app.core.config import (
    CrazyThursdaySource,
    FunContentSource,
    HolidaySource,
    NewsSource,
    StockIndexSource,
    load_config,
)
from app.core.errors import AppError, error_response, get_http_status
from app.core.logging import setup_logging
from app.core.scheduler import TaskScheduler
from app.core.services import AppServices
from app.services.browser import browser_manager
from app.services.cache import CacheCleaner
from app.services.calendar import init_timezones, today_business
from app.services.compute import DataComputer, DomainDataAggregator, TemplateAdapter
from app.services.fetcher import CachedDataFetcher
from app.services.fun_content import CachedFunContentService
from app.services.generator import generate_and_save_image
from app.services.holiday import CachedHolidayService
from app.services.kfc import CachedKfcService
from app.services.renderer import ImageRenderer
from app.services.stock_index import StockIndexService


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager.

    Handles startup and shutdown events for the FastAPI application.
    """
    # ==================== Startup ====================
    print("Starting Moyuren API...")

    # 1. Load configuration
    config = load_config()
    app.state.config = config

    # 2. Setup logging
    logger = setup_logging(config.logging, logger_name="moyuren")
    app.state.logger = logger
    logger.info("Configuration loaded successfully")
    logger.info("Logging initialized")

    # 2.1 Configure browser manager
    browser_manager.configure(logger)
    app.state.browser_manager = browser_manager

    # 2.2 Initialize timezones
    init_timezones(
        business_tz=config.timezone.business,
        display_tz=config.timezone.display
    )

    # 3. Ensure required directories exist
    Path(config.paths.static_dir).mkdir(parents=True, exist_ok=True)
    Path(config.paths.state_path).parent.mkdir(parents=True, exist_ok=True)
    Path(config.logging.file).parent.mkdir(parents=True, exist_ok=True)

    # 3.1 Remount static files with configured path if different from default
    if config.paths.static_dir != _default_static_dir:
        # Remove default mount and add configured one
        app.routes = [r for r in app.routes if getattr(r, "name", None) != "static"]
        app.mount("/static", StaticFiles(directory=config.paths.static_dir), name="static")

    # 4. Initialize services
    # 创建日级缓存目录
    daily_cache_dir = Path(config.paths.state_path).parent / "cache"

    # 创建共享 HTTP 客户端（连接池复用）
    http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(30.0),
        limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
    )
    app.state.http_client = http_client

    # 初始化带缓存的数据获取器
    news_source = config.get_source(NewsSource)
    data_fetcher = CachedDataFetcher(
        source=news_source,
        logger=logger,
        cache_dir=daily_cache_dir,
        http_client=http_client,
    )

    # Initialize data computation components
    domain_aggregator = DomainDataAggregator()
    template_adapter = TemplateAdapter()
    data_computer = DataComputer(domain_aggregator, template_adapter)

    # 初始化节假日服务（原始数据目录保持不变）
    holiday_raw_cache_dir = Path(config.paths.state_path).parent / "holidays"
    holiday_source = config.get_source(HolidaySource)
    holiday_service = CachedHolidayService(
        logger=logger,
        cache_dir=daily_cache_dir,
        raw_cache_dir=holiday_raw_cache_dir,
        mirror_urls=holiday_source.mirror_urls if holiday_source else [],
        timeout_sec=holiday_source.timeout_sec if holiday_source else 10,
    )

    # 初始化带缓存的趣味内容服务
    fun_content_source = config.get_source(FunContentSource)
    fun_content_service = CachedFunContentService(
        config=fun_content_source,
        logger=logger,
        cache_dir=daily_cache_dir,
    )

    # 初始化带缓存的 KFC 服务
    kfc_service = None
    crazy_thursday_source = config.get_source(CrazyThursdaySource)
    if crazy_thursday_source:
        kfc_service = CachedKfcService(
            config=crazy_thursday_source,
            logger=logger,
            cache_dir=daily_cache_dir,
        )

    # Initialize stock index service if config exists
    stock_index_service = None
    stock_index_source = config.get_source(StockIndexSource)
    if stock_index_source:
        stock_index_service = StockIndexService(stock_index_source)


    # Get templates configuration
    templates_config = config.get_templates_config()

    image_renderer = ImageRenderer(
        templates_config=templates_config,
        static_dir=config.paths.static_dir,
        render_config=config.templates.config,
        logger=logger,
    )

    cache_cleaner = CacheCleaner(
        static_dir=config.paths.static_dir,
        ttl_hours=config.output_cache.ttl_hours,
        logger=logger,
    )

    # 创建服务容器
    app.state.services = AppServices(
        data_fetcher=data_fetcher,
        holiday_service=holiday_service,
        fun_content_service=fun_content_service,
        kfc_service=kfc_service,
        stock_index_service=stock_index_service,
        image_renderer=image_renderer,
        data_computer=data_computer,
        cache_cleaner=cache_cleaner,
    )

    # Store services in app.state for access in tasks (兼容层)
    app.state.data_fetcher = data_fetcher
    app.state.data_computer = data_computer
    app.state.domain_aggregator = domain_aggregator
    app.state.template_adapter = template_adapter
    app.state.holiday_service = holiday_service
    app.state.fun_content_service = fun_content_service
    app.state.kfc_service = kfc_service
    app.state.stock_index_service = stock_index_service
    app.state.image_renderer = image_renderer
    app.state.templates_config = templates_config
    app.state.cache_cleaner = cache_cleaner
    app.state.is_refreshing = False  # Initialize background refresh lock

    # 启动预热日级缓存
    async def warmup_daily_cache():
        """预热日级缓存"""
        logger.info("Warming up daily cache...")
        tasks = [
            data_fetcher.get(),
            fun_content_service.get(),
            holiday_service.get(),
        ]
        if kfc_service:
            tasks.append(kfc_service.get())
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.warning(f"Cache warmup task {i} failed: {result}")
        logger.info("Daily cache warmup completed")

    await warmup_daily_cache()

    # 5. Initialize and start scheduler
    scheduler = TaskScheduler(config.scheduler, logger)

    # Add daily image generation task
    async def generate_image_task():
        """Scheduled task: Generate moyuren calendar image."""
        try:
            await generate_and_save_image(app)
        except Exception as e:
            logger.error(f"Image generation task failed: {e}")

    if config.scheduler.mode == "hourly":
        scheduler.add_hourly_job(
            job_id="generate_moyuren_image",
            func=generate_image_task,
            minute=config.scheduler.minute_of_hour,
        )
        logger.info(
            f"Scheduler mode: hourly (minute={config.scheduler.minute_of_hour:02d})"
        )
    else:
        # Add daily image generation tasks for each configured time
        for idx, time_str in enumerate(config.scheduler.daily_times):
            try:
                hour, minute = map(int, time_str.split(":"))
                job_id = f"generate_moyuren_image_{idx}" if len(config.scheduler.daily_times) > 1 else "generate_moyuren_image"
                scheduler.add_daily_job(
                    job_id=job_id,
                    func=generate_image_task,
                    hour=hour,
                    minute=minute,
                )
            except (ValueError, AttributeError) as e:
                logger.warning(f"Invalid time format '{time_str}', skipping: {e}")
        logger.info(f"Scheduler mode: daily (times={config.scheduler.daily_times})")

    scheduler.start()
    app.state.scheduler = scheduler
    logger.info("Scheduler started")

    # 6. Validate and generate initial image if needed
    state_path = Path(config.paths.state_path)
    need_regenerate = False
    cache_is_valid_but_expired = False  # Track if cache is structurally valid but just expired

    if not state_path.exists():
        logger.info("No existing state file found")
        need_regenerate = True
    else:
        # Validate state file content
        try:
            import json
            with state_path.open("r", encoding="utf-8") as f:
                state_data = json.load(f)
            required_fields = ["filename", "date", "updated", "updated_at"]
            missing_fields = [f for f in required_fields if f not in state_data]
            if missing_fields:
                logger.warning(f"State file missing required fields: {missing_fields}")
                need_regenerate = True
            else:
                # Check cache expiration
                try:
                    updated_at_ms = state_data.get("updated_at")
                    if updated_at_ms is None:
                        logger.warning("State file missing 'updated_at' field")
                        need_regenerate = True
                    # Validate updated_at type and range
                    elif not isinstance(updated_at_ms, (int, float)):
                        logger.warning(f"Invalid updated_at type: {type(updated_at_ms).__name__}, expected int or float")
                        need_regenerate = True
                    elif updated_at_ms < 0:
                        logger.warning(f"Invalid updated_at value: {updated_at_ms} (negative timestamp)")
                        need_regenerate = True
                    else:
                        # 使用日期边界判断替代 TTL
                        cached_date = state_data.get("date")
                        today_str = today_business().isoformat()
                        if cached_date != today_str:
                            logger.info(f"Cache date mismatch: {cached_date} != {today_str}")
                            need_regenerate = True
                            cache_is_valid_but_expired = True
                except (TypeError, ValueError, KeyError) as e:
                    logger.warning(f"Failed to check cache expiration: {e}")
                    need_regenerate = True
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"State file invalid or unreadable: {e}")
            need_regenerate = True

    if need_regenerate:
        if cache_is_valid_but_expired:
            # Strategy: Async background refresh (cache is valid but expired)
            # User can temporarily use stale data, fast startup
            logger.info("Cache expired but valid, triggering background refresh...")

            async def _background_refresh():
                """Background task wrapper with error handling and single-flight."""
                if app.state.is_refreshing:
                    logger.info("Background refresh already in progress, skipping")
                    return
                app.state.is_refreshing = True
                try:
                    await generate_and_save_image(app)
                    logger.info("Background refresh completed successfully")
                except asyncio.CancelledError:
                    logger.info("Background refresh cancelled due to shutdown")
                    raise
                except Exception as e:
                    logger.error(f"Background refresh failed: {e}")
                    logger.warning("Stale cache will be used until next scheduled refresh")
                finally:
                    app.state.is_refreshing = False

            task = asyncio.create_task(_background_refresh())
            app.state.background_refresh_task = task  # Save reference for shutdown
            logger.info("Background refresh task created, service starting immediately")
        else:
            # Strategy: Sync generation (cache is missing or invalid)
            # Must generate now to ensure data availability
            if state_path.exists():
                # Invalid cache exists, remove it before regenerating
                logger.info("Removing invalid cache file...")
                try:
                    if state_path.is_file():
                        state_path.unlink()
                    elif state_path.is_dir():
                        logger.error(f"State path is a directory, not a file: {state_path}")
                        import shutil
                        shutil.rmtree(state_path)
                except (OSError, IsADirectoryError) as e:
                    logger.error(f"Failed to remove invalid cache: {e}")

            logger.info("No valid cache found, generating initial image...")
            try:
                await generate_and_save_image(app)
                logger.info("Initial image generated successfully")
            except Exception as e:
                logger.error(f"Initial image generation failed: {e}")
                logger.warning("Service will start without cache, API will trigger on-demand generation")

    # 7. Browser warmup (background, non-blocking)
    async def _browser_warmup():
        """Background task to pre-initialize browser."""
        try:
            await browser_manager.warmup()
        except asyncio.CancelledError:
            logger.info("Browser warmup cancelled due to shutdown")
            raise
        except Exception as e:
            logger.warning(f"Browser warmup failed: {e}")

    app.state.browser_warmup_task = asyncio.create_task(_browser_warmup())

    logger.info("Moyuren API started successfully")

    try:
        yield
    finally:
        # ==================== Shutdown ====================
        logger.info("Shutting down Moyuren API...")

        # Cancel browser warmup task if running
        if hasattr(app.state, "browser_warmup_task"):
            task = app.state.browser_warmup_task
            if not task.done():
                logger.info("Cancelling browser warmup task...")
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

        # Cancel background refresh task if running
        if hasattr(app.state, "background_refresh_task"):
            task = app.state.background_refresh_task
            if not task.done():
                logger.info("Cancelling background refresh task...")
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

        # Stop scheduler
        if hasattr(app.state, "scheduler"):
            app.state.scheduler.shutdown()

        # Shutdown browser manager
        if hasattr(app.state, "browser_manager"):
            try:
                await app.state.browser_manager.shutdown()
            except Exception as e:
                logger.warning(f"Failed to shutdown browser manager: {e}")

        # Close shared HTTP client
        if hasattr(app.state, "http_client"):
            try:
                await app.state.http_client.aclose()
            except Exception as e:
                logger.warning(f"Failed to close http client: {e}")

        logger.info("Moyuren API stopped")


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    """全局 AppError 异常处理器"""
    return JSONResponse(
        status_code=get_http_status(exc.code),
        content=error_response(exc.code, exc.message, exc.detail),
    )


# Create FastAPI application
app = FastAPI(
    title="Moyuren API",
    description="摸鱼日历 API 服务",
    version=__version__,
    lifespan=lifespan,
)

app.add_exception_handler(AppError, app_error_handler)


@app.api_route("/healthz", methods=["GET", "HEAD"])
async def healthz():
    return {"status": "ok"}


# Mount static files directory (will be remounted in lifespan with config path)
# Default mount for initial setup
_default_static_dir = "static"
app.mount("/static", StaticFiles(directory=_default_static_dir), name="static")


# Include API routers
app.include_router(moyuren_router)
