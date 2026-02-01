"""Moyuren API application entry point."""

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
    app.state.image_renderer = image_renderer
    app.state.cache_cleaner = cache_cleaner

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

    # 6. Generate initial image if none exists
    state_path = Path(config.paths.state_path)
    if not state_path.exists():
        logger.info("No existing image found, generating initial image...")
        try:
            await generate_and_save_image(app)
        except Exception as e:
            logger.error(f"Initial image generation failed: {e}")

    logger.info("Moyuren API started successfully")

    yield

    # ==================== Shutdown ====================
    logger.info("Shutting down Moyuren API...")

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
