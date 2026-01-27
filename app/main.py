"""Moyuren API application entry point."""

import asyncio
import json
import logging
import os
import tempfile
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.v1.moyuren import router as moyuren_router
from app.core.config import load_config
from app.core.errors import RenderError, StorageError
from app.core.logging import setup_logging
from app.core.scheduler import TaskScheduler
from app.services.cache import CacheCleaner
from app.services.compute import DataComputer
from app.services.fetcher import DataFetcher
from app.services.renderer import ImageRenderer


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
    app.state.image_renderer = image_renderer
    app.state.cache_cleaner = cache_cleaner

    # 5. Initialize and start scheduler
    scheduler = TaskScheduler(config.scheduler, logger)

    # Add daily image generation task
    async def generate_image_task():
        """Scheduled task: Generate moyuren calendar image."""
        try:
            await _generate_and_save_image(app)
        except Exception as e:
            logger.error(f"Image generation task failed: {e}")

    # Parse daily_time from config (format: "HH:MM")
    try:
        hour, minute = map(int, config.scheduler.daily_time.split(":"))
        scheduler.add_daily_job(
            job_id="generate_moyuren_image",
            func=generate_image_task,
            hour=hour,
            minute=minute,
        )
    except (ValueError, AttributeError) as e:
        logger.warning(f"Invalid daily_time format, using default 06:00: {e}")
        scheduler.add_daily_job(
            job_id="generate_moyuren_image",
            func=generate_image_task,
            hour=6,
            minute=0,
        )

    scheduler.start()
    app.state.scheduler = scheduler
    logger.info("Scheduler started")

    # 6. Generate initial image if none exists
    state_path = Path(config.paths.state_path)
    if not state_path.exists():
        logger.info("No existing image found, generating initial image...")
        try:
            await _generate_and_save_image(app)
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
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


# Mount static files directory (will be remounted in lifespan with config path)
# Default mount for initial setup
_default_static_dir = "static"
app.mount("/static", StaticFiles(directory=_default_static_dir), name="static")


# Include API routers
app.include_router(moyuren_router)


# ==================== Internal Helper Functions ====================

async def _generate_and_save_image(app: FastAPI) -> None:
    """Generate image and update state file.

    This function performs the complete image generation pipeline:
    1. Fetch data from API endpoints
    2. Compute template context
    3. Render HTML to image
    4. Update state/latest.json atomically

    Args:
        app: FastAPI application instance with services in app.state.

    Raises:
        RenderError: If image rendering fails.
        StorageError: If state file update fails.
    """
    logger = app.state.logger
    config = app.state.config

    logger.info("Starting image generation...")

    # 1. Fetch data from all endpoints
    raw_data = await app.state.data_fetcher.fetch_all()
    logger.info(f"Fetched data from {len(raw_data)} endpoints")

    # 2. Compute template context
    template_data = app.state.data_computer.compute(raw_data)
    logger.info("Template data computed")

    # 3. Render image
    filename = await app.state.image_renderer.render(template_data)
    logger.info(f"Image rendered: {filename}")

    # 4. Update state/latest.json atomically
    await _update_state_file(
        state_path=config.paths.state_path,
        filename=filename,
    )

    # 5. Clean up old cache (run in thread to avoid blocking)
    deleted_count = await asyncio.to_thread(app.state.cache_cleaner.cleanup)
    if deleted_count > 0:
        logger.info(f"Cleaned up {deleted_count} expired cache file(s)")


async def _update_state_file(state_path: str, filename: str) -> None:
    """Atomically update the state file with latest image information.

    Args:
        state_path: Path to the state file (e.g., "state/latest.json").
        filename: Generated image filename.

    Raises:
        StorageError: If file write fails.
    """
    def _write_state():
        state_file = Path(state_path)

        # Prepare state data
        now = datetime.now()
        state_data = {
            "date": now.strftime("%Y-%m-%d"),
            "timestamp": now.isoformat(),
            "filename": filename,
        }

        # Atomic write: temp file + rename
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                dir=state_file.parent,
                prefix=".latest_",
                suffix=".tmp",
                delete=False,
            ) as tmp_file:
                json.dump(state_data, tmp_file, ensure_ascii=False, indent=2)
                tmp_path = tmp_file.name

            os.replace(tmp_path, state_file)

        except Exception as e:
            # Clean up temp file if it exists
            if tmp_path and Path(tmp_path).exists():
                Path(tmp_path).unlink(missing_ok=True)
            raise StorageError(
                message=f"Failed to update state file: {state_path}",
                detail=str(e),
            ) from e

    # Run blocking IO in thread
    await asyncio.to_thread(_write_state)
