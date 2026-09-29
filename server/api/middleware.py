"""Middleware components for the FastAPI application."""

import logging
import time
from typing import Callable

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from server.common.http import get_client_ip
from server.common.exceptions import AppException, ErrorCode
from server.common.path_security import PathSecurityError
from server.infrastructure.log_security import safe_log_value

logger = logging.getLogger(__name__)


def setup_exception_handlers(app: FastAPI) -> None:
    """
    Setup global exception handlers for the application.

    Args:
        app: FastAPI application instance
    """

    @app.exception_handler(AppException)
    async def app_exception_handler(request: Request, exc: AppException) -> JSONResponse:
        """Handle application exceptions."""
        logger.warning(
            "AppException: %s - %s",
            safe_log_value(exc.code.value),
            safe_log_value(exc.message),
            extra={
                "error_code": safe_log_value(exc.code.value),
                "path": safe_log_value(request.url.path),
                "method": safe_log_value(request.method),
            },
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=exc.to_dict(),
        )

    @app.exception_handler(PathSecurityError)
    async def path_security_exception_handler(
        request: Request, exc: PathSecurityError
    ) -> JSONResponse:
        """Return a client error for rejected filesystem or URL input."""
        logger.warning(
            "Path security rejection: %s",
            safe_log_value(exc),
            extra={
                "path": safe_log_value(request.url.path),
                "method": safe_log_value(request.method),
            },
        )
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "code": ErrorCode.INVALID_PATH.value,
                    "message": str(exc),
                    "details": {},
                }
            },
        )

    @app.exception_handler(Exception)
    async def general_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        """Handle unexpected exceptions."""
        logger.exception(
            "Unhandled exception: %s: %s",
            safe_log_value(type(exc).__name__),
            safe_log_value(exc),
            extra={
                "path": safe_log_value(request.url.path),
                "method": safe_log_value(request.method),
            },
        )
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": ErrorCode.INTERNAL_ERROR.value,
                    "message": "服务器内部错误",
                    "details": {},
                }
            },
        )


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """
    Middleware for logging HTTP requests and responses.

    Logs request method, path, status code, and response time.
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Process request and log details."""
        start_time = time.perf_counter()

        # Skip logging for health checks and static files
        path = request.url.path
        if path in ("/health", "/favicon.ico") or path.startswith("/static"):
            return await call_next(request)

        # Process request
        response = await call_next(request)

        # Calculate response time
        process_time = (time.perf_counter() - start_time) * 1000

        # Log request details
        logger.info(
            "%s %s - %s (%.1fms)",
            safe_log_value(request.method),
            safe_log_value(path),
            response.status_code,
            process_time,
            extra={
                "method": safe_log_value(request.method),
                "path": safe_log_value(path),
                "status_code": response.status_code,
                "response_time_ms": round(process_time, 1),
                "client_ip": safe_log_value(get_client_ip(request)),
            },
        )

        # Add timing header
        response.headers["X-Response-Time"] = f"{process_time:.1f}ms"

        return response


class CORSDebugMiddleware(BaseHTTPMiddleware):
    """
    Debug middleware for CORS issues.

    Only use in development mode.
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Log CORS-related headers for debugging."""
        origin = request.headers.get("Origin")
        if origin and request.method == "OPTIONS":
            logger.debug(
                "CORS preflight: %s -> %s",
                safe_log_value(origin),
                safe_log_value(request.url.path),
                extra={
                    "origin": safe_log_value(origin),
                    "method": safe_log_value(request.method),
                    "path": safe_log_value(request.url.path),
                },
            )

        return await call_next(request)


def setup_middleware(app: FastAPI, debug: bool = False) -> None:
    """
    Setup all middleware for the application.

    Args:
        app: FastAPI application instance
        debug: Enable debug middleware
    """
    # Request logging (add first so it's executed last)
    app.add_middleware(RequestLoggingMiddleware)

    # Debug middleware
    if debug:
        app.add_middleware(CORSDebugMiddleware)

    logger.info("Middleware configured")
