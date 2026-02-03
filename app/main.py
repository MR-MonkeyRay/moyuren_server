"""Moyuren API application entry point."""

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.api.v1.moyuren import router as moyuren_router
from app.core.config import load_config
from app.core.logging import setup_logging
from app.services.generator import generate_and_save_image
from app.core.scheduler import TaskScheduler
from app.services.cache import CacheCleaner
from app.services.compute import DataComputer
from app.services.fetcher import DataFetcher
from app.services.fun_content import FunContentService
from app.services.kfc import KfcService
from app.services.holiday import HolidayService
from app.services.stock_index import StockIndexService
from app.services.renderer import ImageRenderer
from app.services.calendar import init_timezones


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

    # 2.1 Initialize timezones
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
    data_fetcher = DataFetcher(
        endpoints=config.fetch.api_endpoints,
        logger=logger,
    )

    data_computer = DataComputer()

    holiday_cache_dir = Path(config.paths.state_path).parent / "holidays"
    holiday_service = HolidayService(
        logger=logger,
        cache_dir=holiday_cache_dir,
        mirror_urls=config.holiday.mirror_urls,
        timeout_sec=config.holiday.timeout_sec,
    )

    fun_content_service = FunContentService(config.fun_content)
    
    # Initialize KFC service if config exists (handle cases where config might be missing temporarily)
    kfc_service = None
    if config.crazy_thursday:
        kfc_service = KfcService(config.crazy_thursday)

    # Initialize stock index service if config exists
    stock_index_service = None
    if config.stock_index:
        stock_index_service = StockIndexService(config.stock_index)

    image_renderer = ImageRenderer(
        template_path=config.paths.template_path,
        static_dir=config.paths.static_dir,
        render_config=config.render,
        logger=logger,
    )

    cache_cleaner = CacheCleaner(
        static_dir=config.paths.static_dir,
        ttl_hours=config.cache.ttl_hours,
        logger=logger,
    )

    # Store services in app.state for access in tasks
    app.state.data_fetcher = data_fetcher
    app.state.data_computer = data_computer
    app.state.holiday_service = holiday_service
    app.state.fun_content_service = fun_content_service
    app.state.kfc_service = kfc_service
    app.state.stock_index_service = stock_index_service
    app.state.image_renderer = image_renderer
    app.state.cache_cleaner = cache_cleaner
    app.state.is_refreshing = False  # Initialize background refresh lock

    # 5. Initialize and start scheduler
    scheduler = TaskScheduler(config.scheduler, logger)

    # Add daily image generation task
    async def generate_image_task():
        """Scheduled task: Generate moyuren calendar image."""
        try:
            await generate_and_save_image(app)
        except Exception as e:
            logger.error(f"Image generation task failed: {e}")

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
            import time
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
                        current_time_ms = int(time.time() * 1000)

                        # Check if updated_at is in the future (allow 1 minute tolerance for clock skew)
                        if updated_at_ms > current_time_ms + 60000:
                            logger.warning(f"Invalid updated_at value: {updated_at_ms} (future timestamp)")
                            need_regenerate = True
                        else:
                            age_hours = max((current_time_ms - updated_at_ms) / (1000 * 3600), 0)

                            # Validate TTL configuration and use default if invalid
                            ttl_hours = config.cache.ttl_hours
                            if not isinstance(ttl_hours, (int, float)) or ttl_hours <= 0:
                                ttl_hours = 24  # Use default value
                                logger.warning(f"Invalid cache TTL configuration: {config.cache.ttl_hours}, using default: {ttl_hours} hours")

                            if age_hours > ttl_hours:
                                logger.info(f"Cache expired: {age_hours:.1f} hours old, TTL is {ttl_hours} hours")
                                need_regenerate = True
                                cache_is_valid_but_expired = True  # Mark as valid structure, just expired
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

    logger.info("Moyuren API started successfully")

    yield

    # ==================== Shutdown ====================
    logger.info("Shutting down Moyuren API...")

    # Cancel background refresh task if running
    if hasattr(app.state, "background_refresh_task"):
        task = app.state.background_refresh_task
        if not task.done():
            logger.info("Cancelling background refresh task...")
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    # Stop scheduler
    if hasattr(app.state, "scheduler"):
        app.state.scheduler.shutdown()

    logger.info("Moyuren API stopped")


# Create FastAPI application
app = FastAPI(
    title="Moyuren API",
    description="摸鱼日历 API 服务",
    version=__version__,
    lifespan=lifespan,
)


@app.api_route("/healthz", methods=["GET", "HEAD"])
async def healthz():
    return {"status": "ok"}


# Mount static files directory (will be remounted in lifespan with config path)
# Default mount for initial setup
_default_static_dir = "static"
app.mount("/static", StaticFiles(directory=_default_static_dir), name="static")


# Include API routers
app.include_router(moyuren_router)
