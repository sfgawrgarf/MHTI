"""ConfigService interface definition."""

from typing import Protocol

from server.models.config import ProxyConfig, LanguageConfig


class IConfigService(Protocol):
    """Interface for configuration service."""

    async def get_cookie(self) -> str | None:
        """Get TMDB cookie."""
        ...

    async def get_api_token(self) -> str | None:
        """Get TMDB API token."""
        ...

    async def get_proxy_config(self) -> ProxyConfig:
        """Get proxy configuration."""
        ...

    async def get_language_config(self) -> LanguageConfig:
        """Get language configuration."""
        ...
