"""FastAPI application entry point."""

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import Any, Awaitable

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from server import __version__
from server.core.logging_runtime import get_logging_runtime

logging_runtime = get_logging_runtime()
logging_runtime.bootstrap()
logger = logging.getLogger(__name__)


async def _shutdown_step(
    name: str,
    operation: Awaitable[Any],
    *,
    timeout: float = 15.0,
) -> Any | None:
    """Run one shutdown step without preventing later cleanup."""
    try:
        return await asyncio.wait_for(operation, timeout=timeout)
    except TimeoutError:
        logger.error("Shutdown step timed out: %s", name)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Shutdown step failed: %s", name)
    return None


async def _shutdown_application(watcher: Any | None) -> None:
    """Run every cleanup stage even when an earlier stage fails."""
    logger.info("Shutting down application...")

    from server.services.scrape_job_service import shutdown_workers as shutdown_scrape_workers
    from server.services.manual_job_service import shutdown_workers as shutdown_manual_workers

    await _shutdown_step("manual workers", shutdown_manual_workers())
    if watcher is not None and watcher._running:
        await _shutdown_step("watcher", watcher.stop())
    await _shutdown_step("scrape workers", shutdown_scrape_workers())

    from server.services.file_io import shutdown_file_io

    try:
        shutdown_file_io()
    except Exception:
        logger.exception("Shutdown step failed: file I/O executor")

    await _shutdown_step("service container", cleanup_services())

    # Flush logging while database connections are still available.
    logs_flushed = await _shutdown_step("logging", logging_runtime.shutdown())
    if logs_flushed is not True:
        logger.error("Logging shutdown did not complete cleanly")

    await _shutdown_step("database", close_database())
    logger.info("Application shutdown complete")

# CORS allowed origins (Docker environment)
CORS_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:3500",
    "http://127.0.0.1:3500",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
    "http://localhost",
    "http://127.0.0.1",
]

# Allow all private network origins (192.168.x.x, 10.x.x.x, 172.16-31.x.x)
CORS_ALLOW_ALL_ORIGINS = os.getenv("CORS_ALLOW_ALL", "false").lower() == "true"

# Allow additional origins from environment
if extra_origins := os.getenv("CORS_ORIGINS"):
    CORS_ORIGINS.extend(extra_origins.split(","))

# API Routers
from server.api.ai import router as ai_router
from server.api.auth import router as auth_router
from server.api.config import router as config_router
from server.api.emby import router as emby_router
from server.api.files import router as files_router
from server.api.history import router as history_router
from server.api.images import router as images_router
from server.api.job_runtime import router as job_runtime_router
from server.api.manual_job import router as manual_job_router
from server.api.nfo import router as nfo_router
from server.api.parser import router as parser_router
from server.api.rename import router as rename_router
from server.api.scheduler import router as scheduler_router
from server.api.scrape_job import router as scrape_job_router
from server.api.scraper import router as scraper_router
from server.api.subtitles import router as subtitles_router
from server.api.templates import router as templates_router
from server.api.tmdb import router as tmdb_router
from server.api.watcher import router as watcher_router
from server.api.websocket import router as websocket_router
from server.api.frontend_config import router as frontend_config_router
from server.api.logs import router as logs_router

# Core components
from server.core.container import (
    init_services,
    cleanup_services,
    get_scheduler_service,
    get_watcher_service,
)
from server.core.database import init_database, close_database
from server.core.middleware import setup_exception_handlers, setup_middleware


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events."""
    # Startup
    logger.info("Starting application...")
    watcher = None
    scheduler = None
    try:
        # Initialize database with connection pool
        await init_database()

        # Learn confirmed title/episode aliases from historical manual matches.
        # The backfill is idempotent and never replaces a conflicting alias.
        from server.services.media_alias_service import MediaAliasService
        try:
            learned_aliases = await MediaAliasService().backfill_confirmed_history()
            if learned_aliases:
                logger.info(
                    "Learned %s confirmed media aliases from history",
                    learned_aliases,
                )
        except Exception as exc:
            # Alias learning is an optimization and must never prevent startup.
            logger.warning("Unable to backfill confirmed media aliases: %s", exc)

        # Initialize authentication configuration from database
        from server.core.config import init_auth_config
        await init_auth_config()
        logger.info("Authentication configuration loaded from database")

        # Initialize service container
        await init_services()

        # Recover persisted work before the watcher performs its initial scan.
        from server.services.scrape_job_service import (
            recover_pending_jobs as recover_scrape_jobs,
        )
        from server.services.manual_job_service import (
            recover_pending_jobs as recover_manual_jobs,
        )
        recovered_scrape = await recover_scrape_jobs()
        recovered_manual = await recover_manual_jobs()
        if recovered_scrape or recovered_manual:
            logger.info(
                "Recovered persisted jobs: scrape=%s, manual=%s",
                recovered_scrape,
                recovered_manual,
            )

        scheduler = get_scheduler_service()
        await scheduler.start()
        logger.info("Scheduled task runner started")

        # Initialize the direct database log persistence service.
        from server.core.container import get_log_service
        log_service = get_log_service()

        # Apply all persisted logging settings after the database is available.
        log_config = await log_service.get_config()
        await logging_runtime.apply(log_config, log_service)
        try:
            deleted_logs = await log_service.cleanup_old_logs()
            if deleted_logs:
                logger.info("Cleaned up %s expired log entries", deleted_logs)
        except Exception:
            logger.exception("Unable to clean up expired logs during startup")

        logger.info("Log service started")

        # Auto-start watcher service if enabled folders exist
        watcher = get_watcher_service()
        folders, _ = await watcher.list_folders()
        if any(f.enabled for f in folders):
            logger.info("Detected enabled watch folders, starting watcher service")
            await watcher.start()

        logger.info("Application started successfully")
        yield
    finally:
        if scheduler is not None:
            await _shutdown_step("scheduler", scheduler.stop())
        await _shutdown_application(watcher)


# Create FastAPI application
app = FastAPI(
    title="MHTI API",
    description="API for scanning and scraping TV series metadata",
    version=__version__,
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
)

# Setup global exception handlers
setup_exception_handlers(app)

# Setup middleware stack
DEBUG_MODE = os.getenv("DEBUG", "false").lower() == "true"
setup_middleware(app, debug=DEBUG_MODE)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if CORS_ALLOW_ALL_ORIGINS else CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
    expose_headers=["X-Response-Time", "X-Request-ID"],
)

# Include API routers (each router defines its own /api/* prefix)
app.include_router(auth_router)
app.include_router(ai_router)
app.include_router(files_router)
app.include_router(parser_router)
app.include_router(config_router)
app.include_router(emby_router)
app.include_router(tmdb_router)
app.include_router(nfo_router)
app.include_router(images_router)
app.include_router(job_runtime_router)
app.include_router(templates_router)
app.include_router(rename_router)
app.include_router(subtitles_router)
app.include_router(scheduler_router)
app.include_router(history_router)
app.include_router(watcher_router)
app.include_router(scraper_router)
app.include_router(manual_job_router)
app.include_router(scrape_job_router)
app.include_router(websocket_router)  # WebSocket at /ws
app.include_router(frontend_config_router)  # Frontend runtime config
app.include_router(logs_router)  # Logs management API


@app.get("/health")
async def health_check() -> dict:
    """
    Health check endpoint for Docker/Kubernetes.

    Returns comprehensive health status including:
    - Overall status: healthy, degraded, or unhealthy
    - Database connection status
    - External service configurations
    """
    from server.core.database import get_db_manager

    health_status = {
        "status": "healthy",
        "checks": {
            "database": "unknown",
            "tmdb_configured": "unknown",
            "emby_configured": "unknown",
        },
    }

    # Check database connection
    try:
        manager = await get_db_manager()
        async with manager.get_connection() as db:
            await db.execute("SELECT 1")
        health_status["checks"]["database"] = "healthy"
    except Exception:
        health_status["checks"]["database"] = "unhealthy"
        health_status["status"] = "degraded"

    # Check TMDB configuration (non-blocking)
    try:
        from server.core.container import get_config_service
        config_service = get_config_service()
        tmdb_cookie = await config_service.get_cookie()
        # ConfigService exposes the TMDB token through get_api_token().
        # Calling the old, non-existent name made a configured instance report
        # a misleading check_failed health status.
        tmdb_token = await config_service.get_api_token()
        if tmdb_cookie and tmdb_token:
            health_status["checks"]["tmdb_configured"] = "configured"
        elif tmdb_cookie or tmdb_token:
            health_status["checks"]["tmdb_configured"] = "partial"
        else:
            health_status["checks"]["tmdb_configured"] = "not_configured"
    except Exception:
        health_status["checks"]["tmdb_configured"] = "check_failed"

    # Check Emby configuration (non-blocking)
    try:
        from server.core.container import get_emby_service
        emby_service = get_emby_service()
        emby_config = await emby_service.get_config()
        health_status["checks"]["emby_configured"] = "configured" if emby_config.enabled else "disabled"
    except Exception:
        health_status["checks"]["emby_configured"] = "check_failed"

    return health_status


@app.get("/health/live")
async def liveness_check() -> dict[str, str]:
    """Kubernetes liveness probe - checks if app is running."""
    return {"status": "alive"}


@app.get("/health/ready")
async def readiness_check() -> dict:
    """
    Kubernetes readiness probe - checks if app is ready to serve traffic.

    Returns unhealthy if database is not accessible.
    """
    from server.core.database import get_db_manager

    try:
        manager = await get_db_manager()
        async with manager.get_connection() as db:
            await db.execute("SELECT 1")
        return {"status": "ready"}
    except Exception:
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "reason": "database_unavailable"},
        )


@app.get("/")
async def root() -> dict[str, str]:
    """Root endpoint with API information."""
    return {
        "name": "MHTI API",
        "version": __version__,
        "docs": "/api/docs",
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "server.main:app",
        host="127.0.0.1",
        port=8000,
        reload=True,
        reload_dirs=["server"],
    )
