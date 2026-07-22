"""FastAPI application entry point."""

import logging
import os
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# 日志目录
LOG_DIR = Path(__file__).parent.parent / "data" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

# 日志格式
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

# Configure root logger
logging.basicConfig(
    level=logging.INFO,
    format=LOG_FORMAT,
)
logger = logging.getLogger(__name__)


def setup_file_logging() -> RotatingFileHandler | None:
    """设置文件日志处理器（带轮转）。"""
    try:
        file_handler = RotatingFileHandler(
            LOG_DIR / "app.log",
            maxBytes=10 * 1024 * 1024,  # 10 MB
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(logging.Formatter(LOG_FORMAT))
        logging.getLogger().addHandler(file_handler)
        logger.info(f"File logging enabled: {LOG_DIR / 'app.log'}")
        return file_handler
    except Exception as e:
        logger.warning(f"Failed to setup file logging: {e}")
        return None

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
CORS_ALLOW_ALL_ORIGINS = os.getenv("CORS_ALLOW_ALL", "true").lower() == "true"

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
from server.core.container import init_services, cleanup_services, get_watcher_service
from server.core.database import init_database, close_database
from server.core.middleware import setup_exception_handlers, setup_middleware


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events."""
    # Startup
    logger.info("Starting application...")

    # Setup file logging (with rotation)
    file_handler = setup_file_logging()

    # Initialize database with connection pool
    await init_database()

    # Initialize authentication configuration from database
    from server.core.config import init_auth_config
    await init_auth_config()
    logger.info("Authentication configuration loaded from database")

    # Initialize service container
    await init_services()

    # Initialize and start log service
    from server.core.container import get_log_service
    log_service = get_log_service()
    await log_service.start()

    # Setup database log handler (仅记录 WARNING 及以上级别，减少性能开销)
    from server.core.log_handler import DatabaseLogHandler
    db_log_handler = DatabaseLogHandler(log_service, batch_size=50, flush_interval=10.0)
    db_log_handler.setLevel(logging.WARNING)  # 只记录警告和错误
    db_log_handler.setFormatter(logging.Formatter(LOG_FORMAT))
    logging.getLogger().addHandler(db_log_handler)
    db_log_handler.start()

    logger.info("Log service started")

    # Auto-start watcher service if enabled folders exist
    watcher = get_watcher_service()
    folders, _ = await watcher.list_folders()
    if any(f.enabled for f in folders):
        logger.info("Detected enabled watch folders, starting watcher service")
        await watcher.start()

    logger.info("Application started successfully")

    yield

    # Shutdown
    logger.info("Shutting down application...")

    # 取消后台 worker（刮削 + 手动任务），避免它们阻塞在队列上导致退出卡顿
    from server.services.scrape_job_service import shutdown_workers as shutdown_scrape_workers
    from server.services.manual_job_service import shutdown_workers as shutdown_manual_workers
    await shutdown_scrape_workers()
    await shutdown_manual_workers()

    # Stop watcher service
    if watcher._running:
        await watcher.stop()

    # Stop database log handler
    db_log_handler.stop()
    logging.getLogger().removeHandler(db_log_handler)

    # Stop log service (flush remaining logs)
    await log_service.stop()
    logger.info("Log service stopped")

    # Remove file handler
    if file_handler:
        logging.getLogger().removeHandler(file_handler)
        file_handler.close()

    # Cleanup services
    await cleanup_services()

    # Close database connections
    await close_database()

    logger.info("Application shutdown complete")


# Create FastAPI application
app = FastAPI(
    title="MHTI API",
    description="API for scanning and scraping TV series metadata",
    version="1.0.0",
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
    except Exception as e:
        health_status["checks"]["database"] = f"unhealthy: {str(e)}"
        health_status["status"] = "degraded"

    # Check TMDB configuration (non-blocking)
    try:
        from server.core.container import get_config_service
        config_service = get_config_service()
        tmdb_cookie = await config_service.get_tmdb_cookie()
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
    except Exception as e:
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "reason": str(e)}
        )


@app.get("/")
async def root() -> dict[str, str]:
    """Root endpoint with API information."""
    return {
        "name": "MHTI API",
        "version": "1.0.0",
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
