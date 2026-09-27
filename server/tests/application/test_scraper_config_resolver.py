"""Tests for effective scraper configuration and credential gates."""

from types import SimpleNamespace

import pytest

from server.application.scraping.config_resolver import ScraperConfigResolver
from server.common.exceptions import TMDBInvalidCredentialsError, TMDBNotConfiguredError
from server.models.download import DownloadConfig
from server.models.manual_job import ManualJobAdvancedSettings
from server.models.nfo import NfoConfig


class FakeConfigService:
    async def get_download_config(self) -> DownloadConfig:
        return DownloadConfig(
            series_poster=False,
            episode_thumb=True,
            series_backdrop=False,
            overwrite_existing=True,
        )

    async def get_nfo_config(self) -> NfoConfig:
        return NfoConfig(enabled=False)


class FakeTMDBService:
    def __init__(self) -> None:
        self.cookie_status = SimpleNamespace(is_configured=True, is_valid=True)
        self.token_status = SimpleNamespace(is_configured=True, is_valid=True)

    async def get_cookie_status(self):
        return self.cookie_status

    async def get_api_token_status(self):
        return self.token_status


@pytest.mark.asyncio
async def test_effective_configs_choose_global_or_task_values() -> None:
    resolver = ScraperConfigResolver(FakeConfigService(), FakeTMDBService())

    global_download = await resolver.get_effective_download_config(None)
    assert global_download == {
        "download_poster": False,
        "download_thumb": True,
        "download_fanart": False,
        "overwrite_existing": True,
    }
    global_nfo = await resolver.get_effective_nfo_config(None)
    assert global_nfo == {"nfo_enabled": False}

    task_settings = ManualJobAdvancedSettings(
        use_global_download=False,
        download_poster=True,
        download_thumb=False,
        download_fanart=True,
        overwrite_image=True,
        use_global_metadata=False,
        nfo_enabled=True,
    )
    assert await resolver.get_effective_download_config(task_settings) == {
        "download_poster": True,
        "download_thumb": False,
        "download_fanart": True,
        "overwrite_existing": True,
    }
    assert await resolver.get_effective_nfo_config(task_settings) == {"nfo_enabled": True}


@pytest.mark.asyncio
async def test_config_checks_and_exceptions_cover_cookie_and_token_states() -> None:
    tmdb = FakeTMDBService()
    resolver = ScraperConfigResolver(FakeConfigService(), tmdb)

    tmdb.cookie_status = SimpleNamespace(is_configured=False, is_valid=None)
    assert await resolver.check_config() == (False, "请先配置 TMDB Cookie")
    with pytest.raises(TMDBNotConfiguredError):
        await resolver.ensure_config_ready()

    tmdb.cookie_status = SimpleNamespace(is_configured=True, is_valid=False)
    assert await resolver.check_config() == (False, "TMDB Cookie 已失效，请更新")
    with pytest.raises(TMDBInvalidCredentialsError):
        await resolver.ensure_config_ready()

    tmdb.cookie_status = SimpleNamespace(is_configured=True, is_valid=True)
    tmdb.token_status = SimpleNamespace(is_configured=False, is_valid=None)
    assert await resolver.check_config() == (False, "请先配置 TMDB API Token")
    with pytest.raises(TMDBNotConfiguredError):
        await resolver.ensure_config_ready()
    with pytest.raises(TMDBNotConfiguredError):
        await resolver.ensure_api_token_ready()

    tmdb.token_status = SimpleNamespace(is_configured=True, is_valid=False)
    assert await resolver.check_config() == (False, "TMDB API Token 已失效，请更新")
    with pytest.raises(TMDBInvalidCredentialsError):
        await resolver.ensure_config_ready()
    with pytest.raises(TMDBInvalidCredentialsError):
        await resolver.ensure_api_token_ready()

    tmdb.token_status = SimpleNamespace(is_configured=True, is_valid=True)
    assert await resolver.check_config() == (True, None)
    await resolver.ensure_config_ready()
    await resolver.ensure_api_token_ready()
