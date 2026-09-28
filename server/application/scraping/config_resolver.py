"""Scraper 配置解析 - 配置检查与有效配置获取。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from server.models.download import DownloadConfig
from server.models.manual_job import ManualJobAdvancedSettings
from server.models.nfo import NfoConfig
from server.models.organize import OrganizeConfig
from server.models.template import NamingTemplate

if TYPE_CHECKING:
    from server.domain.system.config_service import ConfigService
    from server.domain.metadata.tmdb_service import TMDBService


class ScraperConfigResolver:
    """配置管理，提供配置检查和获取方法。"""

    def __init__(self, config_service: ConfigService, tmdb_service: TMDBService) -> None:
        self.config_service = config_service
        self.tmdb_service = tmdb_service

    async def get_effective_download_config(
        self,
        advanced_settings: ManualJobAdvancedSettings | None
    ) -> dict:
        """获取有效的下载配置（任务设置优先于全局配置）。

        Args:
            advanced_settings: 可选的高级设置。

        Returns:
            包含 download_poster, download_thumb, download_fanart 的配置字典。
        """
        # 如果没有高级设置或使用全局配置
        if advanced_settings is None or advanced_settings.use_global_download:
            global_config = await self.config_service.get_download_config()
            return {
                "download_poster": global_config.series_poster,
                "download_thumb": global_config.episode_thumb,
                "download_fanart": global_config.series_backdrop,
                "overwrite_existing": global_config.overwrite_existing,
            }
        else:
            # 使用任务级设置
            return {
                "download_poster": advanced_settings.download_poster,
                "download_thumb": advanced_settings.download_thumb,
                "download_fanart": advanced_settings.download_fanart,
                "overwrite_existing": advanced_settings.overwrite_image,
            }

    async def get_effective_image_config(
        self,
        advanced_settings: ManualJobAdvancedSettings | None,
    ) -> dict:
        """Return the complete image configuration used by the media pipeline."""
        if advanced_settings is None or advanced_settings.use_global_download:
            return (await self.config_service.get_download_config()).model_dump()

        defaults = DownloadConfig()
        return {
            "series_poster": advanced_settings.download_poster,
            "series_backdrop": advanced_settings.download_fanart,
            "series_logo": defaults.series_logo,
            "series_banner": defaults.series_banner,
            "season_poster": defaults.season_poster,
            "episode_thumb": advanced_settings.download_thumb,
            "extra_backdrops": defaults.extra_backdrops,
            "extra_backdrops_count": defaults.extra_backdrops_count,
            "poster_quality": defaults.poster_quality.value,
            "backdrop_quality": defaults.backdrop_quality.value,
            "thumb_quality": defaults.thumb_quality.value,
            "overwrite_existing": advanced_settings.overwrite_image,
        }

    async def get_effective_organize_config(
        self,
        advanced_settings: ManualJobAdvancedSettings | None,
    ) -> OrganizeConfig | None:
        """Return effective organize filters without inventing saved defaults."""
        if advanced_settings is not None and not advanced_settings.use_global_organize:
            return OrganizeConfig(
                min_file_size_mb=advanced_settings.file_size_filter,
                file_type_whitelist=(
                    advanced_settings.file_ext_whitelist
                    + advanced_settings.extra_ext_whitelist
                ),
                filename_blacklist=advanced_settings.file_name_blacklist,
                junk_pattern_filter=advanced_settings.file_sanitize_list,
            )

        from server.domain.system.config_service import ORGANIZE_CONFIG_KEY

        if not await self.config_service.exists(ORGANIZE_CONFIG_KEY):
            return None
        return await self.config_service.get_organize_config()

    async def get_effective_naming_config(
        self,
        advanced_settings: ManualJobAdvancedSettings | None,
    ) -> NamingTemplate:
        """Return task-level naming templates when global naming is disabled."""
        if advanced_settings is None or advanced_settings.use_global_naming:
            return await self.config_service.get_naming_config()

        defaults = NamingTemplate()
        return NamingTemplate(
            series_folder=(
                advanced_settings.series_folder_template.strip()
                or defaults.series_folder
            ),
            season_folder=(
                advanced_settings.season_folder_template.strip()
                or defaults.season_folder
            ),
            episode_file=(
                advanced_settings.episode_file_template.strip()
                or defaults.episode_file
            ),
        )

    async def get_effective_nfo_config(
        self,
        advanced_settings: ManualJobAdvancedSettings | None
    ) -> dict:
        """获取有效的 NFO 配置。

        Args:
            advanced_settings: 可选的高级设置。

        Returns:
            包含总开关和剧集/季/集字段开关的配置字典。
        """
        if advanced_settings is None or advanced_settings.use_global_metadata:
            global_config = await self.config_service.get_nfo_config()
            return {
                "nfo_enabled": global_config.enabled,
                "tvshow": global_config.tvshow.model_dump(),
                "season": global_config.season.model_dump(),
                "episode": global_config.episode.model_dump(),
            }
        else:
            default_config = NfoConfig()
            return {
                "nfo_enabled": advanced_settings.nfo_enabled,
                "tvshow": default_config.tvshow.model_dump(),
                "season": default_config.season.model_dump(),
                "episode": default_config.episode.model_dump(),
            }

    async def check_config(self) -> tuple[bool, str | None]:
        """检查必要配置是否已就绪。

        Returns:
            (is_ready, error_message) 元组。
        """
        # Check Cookie
        cookie_status = await self.tmdb_service.get_cookie_status()
        if not cookie_status.is_configured:
            return False, "请先配置 TMDB Cookie"
        if cookie_status.is_valid is False:
            return False, "TMDB Cookie 已失效，请更新"

        # Check API Token
        token_status = await self.tmdb_service.get_api_token_status()
        if not token_status.is_configured:
            return False, "请先配置 TMDB API Token"
        if token_status.is_valid is False:
            return False, "TMDB API Token 已失效，请更新"

        return True, None

    async def ensure_config_ready(self) -> None:
        """确保必要配置已就绪。

        Raises:
            TMDBNotConfiguredError: Cookie 或 API Token 未配置。
            TMDBInvalidCredentialsError: Cookie 或 API Token 已失效。
        """
        from server.common.exceptions import (
            TMDBNotConfiguredError,
            TMDBInvalidCredentialsError,
        )

        # Check Cookie
        cookie_status = await self.tmdb_service.get_cookie_status()
        if not cookie_status.is_configured:
            raise TMDBNotConfiguredError("Cookie")
        if cookie_status.is_valid is False:
            raise TMDBInvalidCredentialsError("Cookie")

        # Check API Token
        token_status = await self.tmdb_service.get_api_token_status()
        if not token_status.is_configured:
            raise TMDBNotConfiguredError("API Token")
        if token_status.is_valid is False:
            raise TMDBInvalidCredentialsError("API Token")

    async def ensure_api_token_ready(self) -> None:
        """确保 API Token 已配置且有效。

        Raises:
            TMDBNotConfiguredError: API Token 未配置。
            TMDBInvalidCredentialsError: API Token 已失效。
        """
        from server.common.exceptions import (
            TMDBNotConfiguredError,
            TMDBInvalidCredentialsError,
        )

        token_status = await self.tmdb_service.get_api_token_status()
        if not token_status.is_configured:
            raise TMDBNotConfiguredError("API Token")
        if token_status.is_valid is False:
            raise TMDBInvalidCredentialsError("API Token")
