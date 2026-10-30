"""Moyuren API application entry point."""

import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.routing import Mount

from app import __version__
from app.api.metrics import router as metrics_router
from app.api.v1.moyuren import router as moyuren_router
from app.api.v1.ops import router as ops_router
from app.api.v1.templates import router as templates_router
from app.core.bootstrap import (
    build_services,
    ensure_today_data,
    register_scheduled_jobs,
    warmup_daily_cache,
)
from app.core.config import load_config
from app.core.errors import AppError, error_response, get_http_status
from app.core.logging import setup_logging
from app.core.network import httpx_client_kwargs, safe_exception_for_log
from app.core.scheduler import TaskScheduler
from app.services.browser import browser_manager
from app.services.calendar import get_business_timezone, init_timezones, today_business
from app.services.generator import generate_and_save_image


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Start and stop application services."""
    app.state.startup_state = "starting"
    app.state.startup_started_at = datetime.now(timezone.utc)
    app.state.startup_completed_at = None

    config = load_config()
    app.state.config = config
    logger = setup_logging(config.logging, logger_name="moyuren")
    app.state.logger = logger
    logger.info("Starting Moyuren API...")
    logger.info("Configuration loaded successfully")
    logger.info("Logging initialized")

    browser_manager.configure(logger, proxy_url=config.network.proxy_url)
    app.state.browser_manager = browser_manager
    init_timezones(
        business_tz=config.timezone.business, display_tz=config.timezone.display
    )

    cache_dir = Path(config.paths.cache_dir)
    (cache_dir / "data").mkdir(parents=True, exist_ok=True)
    (cache_dir / "images").mkdir(parents=True, exist_ok=True)
    (cache_dir / "holidays").mkdir(parents=True, exist_ok=True)
    Path(config.logging.file).parent.mkdir(parents=True, exist_ok=True)
    app.router.routes[:] = [
        route
        for route in app.router.routes
        if not (
            isinstance(route, Mount)
            and route.path == "/static"
            and getattr(route, "name", None) == "static"
        )
    ]
    app.mount(
        "/static", StaticFiles(directory=str(cache_dir / "images")), name="static"
    )

    http_client = httpx.AsyncClient(
        **httpx_client_kwargs(
            timeout=httpx.Timeout(30.0),
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
            proxy_url=config.network.proxy_url,
        )
    )
    app.state.http_client = http_client
    services = build_services(config, logger, http_client)
    app.state.services = services

    scheduler = TaskScheduler(config.scheduler, logger, timezone=get_business_timezone())

    async def generate_image_task() -> None:
        """Generate the scheduled calendar image."""
        try:
            await generate_and_save_image(app)
        except Exception as exc:
            logger.error(
                "Image generation task failed: %s",
                safe_exception_for_log(exc, config.network.proxy_url),
            )

    register_scheduled_jobs(scheduler, config, generate_image_task, logger)
    scheduler.start()
    app.state.scheduler = scheduler
    logger.info("Scheduler started")

    async def startup() -> None:
        """Warm daily caches before validating the initial image cache."""
        try:
            await warmup_daily_cache(services, config, logger)
            await ensure_today_data(app)
        except asyncio.CancelledError:
            logger.info("Startup task cancelled due to shutdown")
            raise
        except Exception as exc:
            app.state.startup_state = "failed"
            logger.error(
                "Startup task failed: %s",
                safe_exception_for_log(exc, config.network.proxy_url),
            )
        else:
            app.state.startup_state = "ready"
        finally:
            app.state.startup_completed_at = datetime.now(timezone.utc)

    app.state.startup_task = asyncio.create_task(startup())

    async def browser_warmup() -> None:
        """Pre-initialize the browser in the background."""
        try:
            await browser_manager.warmup()
        except asyncio.CancelledError:
            logger.info("Browser warmup cancelled due to shutdown")
            raise
        except Exception as exc:
            logger.warning(
                "Browser warmup failed: %s",
                safe_exception_for_log(exc, config.network.proxy_url),
            )

    app.state.browser_warmup_task = asyncio.create_task(browser_warmup())
    logger.info("Moyuren API started successfully")

    try:
        yield
    finally:
        logger.info("Shutting down Moyuren API...")
        startup_task = app.state.startup_task
        if not startup_task.done():
            logger.info("Cancelling startup task...")
            startup_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await startup_task

        try:
            await services.generator.shutdown()
        except Exception as exc:
            logger.warning(
                "Failed to shutdown image generator: %s", safe_exception_for_log(exc)
            )

        warmup_task = app.state.browser_warmup_task
        if not warmup_task.done():
            logger.info("Cancelling browser warmup task...")
            warmup_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await warmup_task

        scheduler.shutdown()
        if services.stock_index_service is not None:
            try:
                await services.stock_index_service.close()
            except Exception as exc:
                logger.warning(
                    "Failed to close stock index service: %s",
                    safe_exception_for_log(exc),
                )
        try:
            await browser_manager.shutdown()
        except Exception as exc:
            logger.warning(
                "Failed to shutdown browser manager: %s", safe_exception_for_log(exc)
            )
        try:
            await http_client.aclose()
        except Exception as exc:
            logger.warning(
                "Failed to close http client: %s", safe_exception_for_log(exc)
            )
        logger.info("Moyuren API stopped")


async def app_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Handle application errors with the standard API response."""
    if not isinstance(exc, AppError):
        raise exc
    return JSONResponse(
        status_code=get_http_status(exc.code),
        content=error_response(exc.code, exc.message),
    )


app = FastAPI(
    title="Moyuren API",
    description="摸鱼日历 API 服务",
    version=__version__,
    lifespan=lifespan,
)
app.add_exception_handler(AppError, app_error_handler)


@app.api_route("/healthz", methods=["GET", "HEAD"])
async def healthz() -> dict[str, str]:
    """Return basic liveness status."""
    return {"status": "ok"}


@app.api_route("/readyz", methods=["GET", "HEAD"])
async def readyz(request: Request) -> JSONResponse:
    """Check whether the service can process requests."""
    config = getattr(request.app.state, "config", None)
    startup_state = getattr(request.app.state, "startup_state", "starting")
    manager = getattr(request.app.state, "browser_manager", None)
    checks: dict[str, bool | str] = {
        "config": config is not None,
        "cache_dir": False,
        "startup": startup_state,
        "today_data": False,
        "browser": manager is not None and manager.is_ready,
    }
    if config is not None:
        try:
            cache_dir = Path(config.paths.cache_dir)
            checks["cache_dir"] = cache_dir.exists() and (cache_dir / "data").exists()
            checks["today_data"] = (
                cache_dir / "data" / f"{today_business().isoformat()}.json"
            ).is_file()
        except Exception:  # nosec B110 - readyz checks remain false on failure
            pass

    can_serve = checks["config"] and checks["cache_dir"] and startup_state != "starting"
    status = "ready" if checks["today_data"] and checks["browser"] else "degraded"
    return JSONResponse(
        content={"status": status if can_serve else "not_ready", "checks": checks},
        status_code=200 if can_serve else 503,
    )


app.include_router(moyuren_router)
app.include_router(ops_router)
app.include_router(templates_router)
app.include_router(metrics_router)
