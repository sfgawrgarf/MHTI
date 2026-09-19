"""Regression tests for global watcher configuration synchronization."""

from unittest.mock import AsyncMock, Mock

import pytest

from server.api import config as config_api
from server.models.watcher import (
    WatchedFolder,
    WatcherConfigRequest,
    WatcherMode,
)


def test_effective_mode_respects_provider_capabilities() -> None:
    assert config_api._effective_watcher_mode(
        "/media/tv", WatcherMode.EVENT
    ) == WatcherMode.COMPAT
    assert config_api._effective_watcher_mode(
        "/115网盘/电视剧", WatcherMode.REALTIME
    ) == WatcherMode.COMPAT
    assert config_api._effective_watcher_mode(
        "/115网盘/电视剧", WatcherMode.EVENT
    ) == WatcherMode.EVENT


@pytest.mark.asyncio
async def test_existing_folder_mode_and_performance_profile_are_synchronized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    existing = WatchedFolder(
        id="folder-1",
        path="/media/tv",
        enabled=True,
        mode=WatcherMode.REALTIME,
        scan_interval_seconds=60,
    )
    watcher = Mock()
    watcher.list_folders = AsyncMock(return_value=([existing], 1))
    watcher.update_folder = AsyncMock(return_value=existing)
    watcher.create_folder = AsyncMock()
    watcher.delete_folder = AsyncMock()
    watcher.start = AsyncMock()
    watcher.stop = AsyncMock()
    monkeypatch.setattr(config_api, "get_watcher_service", lambda: watcher)

    config_service = Mock()
    config_service.save_watcher_config = AsyncMock()
    request = WatcherConfigRequest(
        enabled=True,
        mode=WatcherMode.COMPAT,
        performance_mode=True,
        watch_dirs=[existing.path],
    )

    response = await config_api.save_watcher_config(request, config_service)

    watcher.update_folder.assert_awaited_once()
    folder_id, update = watcher.update_folder.await_args.args
    assert folder_id == existing.id
    assert update.mode == WatcherMode.COMPAT
    assert update.scan_interval_seconds == 300
    assert update.enabled is True
    watcher.create_folder.assert_not_awaited()
    watcher.start.assert_awaited_once_with()
    assert response.performance_mode is True


@pytest.mark.asyncio
async def test_new_local_folder_uses_compat_for_global_event_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    watcher = Mock()
    watcher.list_folders = AsyncMock(return_value=([], 0))
    watcher.update_folder = AsyncMock()
    watcher.create_folder = AsyncMock()
    watcher.delete_folder = AsyncMock()
    watcher.start = AsyncMock()
    watcher.stop = AsyncMock()
    monkeypatch.setattr(config_api, "get_watcher_service", lambda: watcher)

    config_service = Mock()
    config_service.save_watcher_config = AsyncMock()
    request = WatcherConfigRequest(
        enabled=True,
        mode=WatcherMode.EVENT,
        performance_mode=False,
        watch_dirs=["/media/tv"],
    )

    await config_api.save_watcher_config(request, config_service)

    create = watcher.create_folder.await_args.args[0]
    assert create.provider == "local"
    assert create.mode == WatcherMode.COMPAT
    assert create.scan_interval_seconds == 60
