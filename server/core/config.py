"""Application configuration - database-backed for auth, static for paths."""

import asyncio
import logging
import secrets
from pathlib import Path


from server.models.auth import AuthConfig

logger = logging.getLogger(__name__)

# Project root directory
_PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()

# Data directory (fixed under project root)
DATA_DIR = _PROJECT_ROOT / "data"


class AppConfig:
    """Application configuration - static paths, database-backed auth."""

    _auth_config_cache: AuthConfig | None = None
    _cache_lock: asyncio.Lock = asyncio.Lock()

    @property
    def data_dir(self) -> Path:
        """Data directory path (fixed)."""
        return DATA_DIR

    @property
    def auth(self) -> AuthConfig:
        """
        Get authentication configuration synchronously.

        For initial app startup, returns default config.
        Use get_auth_async() for database-backed config.
        """
        if self._auth_config_cache:
            return self._auth_config_cache

        # Return default config for sync access before DB is ready
        return AuthConfig(
            max_login_attempts=5,
            lockout_minutes=15,
            jwt_secret=self._get_fallback_jwt_secret(),
            access_token_minutes=15,
            max_sessions=10,
        )

    async def get_auth_async(self) -> AuthConfig:
        """
        Get authentication configuration from database (async).

        This is the preferred method after database is initialized.
        """
        async with self._cache_lock:
            if self._auth_config_cache:
                return self._auth_config_cache

            from server.services.auth_config_service import get_auth_config_service_async
            service = await get_auth_config_service_async()
            self._auth_config_cache = await service.get_auth_config()
            return self._auth_config_cache

    def refresh_auth_cache(self) -> None:
        """Clear auth config cache to force reload from database."""
        self._auth_config_cache = None

    def _get_fallback_jwt_secret(self) -> str:
        """
        Get fallback JWT secret for sync access.

        This is only used during initial startup before DB is available.
        """
        # Generate a temporary secret for initial startup
        return secrets.token_urlsafe(32)


# Singleton instance
_app_config: AppConfig | None = None


def get_app_config() -> AppConfig:
    """Get application configuration (singleton)."""
    global _app_config
    if _app_config is None:
        _app_config = AppConfig()
    return _app_config


async def init_auth_config() -> AuthConfig:
    """
    Initialize authentication configuration from database.

    Call this after database is initialized to load auth config.
    """
    config = get_app_config()
    return await config.get_auth_async()


def clear_auth_config_cache() -> None:
    """Clear auth config cache."""
    if _app_config:
        _app_config.refresh_auth_cache()
