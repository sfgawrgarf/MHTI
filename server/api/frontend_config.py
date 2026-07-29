"""Frontend runtime configuration endpoint."""

import os

from fastapi import APIRouter

from server import __version__

router = APIRouter(prefix="/api/config", tags=["frontend-config"])


@router.get("/frontend")
async def get_frontend_config() -> dict:
    """
    Get frontend runtime configuration.

    Allows frontend to fetch configuration at runtime
    instead of build time.
    """
    return {
        "apiBaseUrl": os.getenv("API_BASE_URL", ""),
        "appName": os.getenv("APP_NAME", "MHTI"),
        "version": os.getenv("APP_VERSION", __version__),
    }
