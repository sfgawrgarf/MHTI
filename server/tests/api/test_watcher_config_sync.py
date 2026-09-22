"""Regression tests for atomic global watcher configuration synchronization."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

from server.api import config as config_api
from server.models.watcher import (
    WatchedFolder,
    WatcherConfig,
    WatcherConfigRequest,
    WatcherMode,
)


def _watcher(existing: list[WatchedFolder], *, running: bool = False) -> Mock:
    watcher = Mock()
    watcher._running = running
    watcher.list_folders = AsyncMock(return_value=(existing, len(existing)))
    watcher.replace_folders = AsyncMock()
    watcher.start = AsyncMock()
    watcher.stop = AsyncMock()
    return watcher


def _config_service(old: WatcherConfig | None = None) -> Mock:
    service = Mock()
    service.get_watcher_config = AsyncMock(return_value=old or WatcherConfig())
    service.save_watcher_config = AsyncMock()
    return service


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
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    media = tmp_path / "media"
    media.mkdir()
    monkeypatch.setenv("MHTI_ALLOWED_MEDIA_ROOTS", str(tmp_path))
    existing = WatchedFolder(
        id="folder-1",
        path=str(media),
        enabled=True,
        mode=WatcherMode.REALTIME,
        scan_interval_seconds=60,
    )
    watcher = _watcher([existing])
    monkeypatch.setattr(config_api, "get_watcher_service", lambda: watcher)
    config_service = _config_service()

    response = await config_api.save_watcher_config(
        WatcherConfigRequest(
            enabled=True,
            mode=WatcherMode.COMPAT,
            performance_mode=True,
            watch_dirs=[str(media)],
        ),
        config_service,
    )

    desired = watcher.replace_folders.await_args.args[0]
    assert len(desired) == 1
    assert desired[0].id == existing.id
    assert desired[0].mode == WatcherMode.COMPAT
    assert desired[0].scan_interval_seconds == 300
    watcher.start.assert_awaited_once_with()
    assert response.performance_mode is True


@pytest.mark.asyncio
async def test_new_local_folder_uses_compat_for_global_event_mode(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    media = tmp_path / "media"
    media.mkdir()
    monkeypatch.setenv("MHTI_ALLOWED_MEDIA_ROOTS", str(tmp_path))
    watcher = _watcher([])
    monkeypatch.setattr(config_api, "get_watcher_service", lambda: watcher)

    await config_api.save_watcher_config(
        WatcherConfigRequest(
            enabled=True,
            mode=WatcherMode.EVENT,
            watch_dirs=[str(media)],
        ),
        _config_service(),
    )

    created = watcher.replace_folders.await_args.args[0][0]
    assert created.provider == "local"
    assert created.mode == WatcherMode.COMPAT
    assert created.scan_interval_seconds == 60


@pytest.mark.asyncio
async def test_local_folder_outside_allowed_roots_is_rejected_before_persisting(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    monkeypatch.setenv("MHTI_ALLOWED_MEDIA_ROOTS", str(allowed))
    watcher = _watcher([])
    monkeypatch.setattr(config_api, "get_watcher_service", lambda: watcher)
    config_service = _config_service()

    with pytest.raises(HTTPException) as exc_info:
        await config_api.save_watcher_config(
            WatcherConfigRequest(enabled=True, watch_dirs=[str(outside)]),
            config_service,
        )

    assert exc_info.value.status_code == 400
    watcher.replace_folders.assert_not_awaited()
    config_service.save_watcher_config.assert_not_awaited()


@pytest.mark.asyncio
async def test_local_folder_symlink_is_persisted_as_its_validated_target(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    media = tmp_path / "media"
    media.mkdir()
    alias = tmp_path / "media-alias"
    alias.symlink_to(media, target_is_directory=True)
    monkeypatch.setenv("MHTI_ALLOWED_MEDIA_ROOTS", str(tmp_path))
    watcher = _watcher([])
    monkeypatch.setattr(config_api, "get_watcher_service", lambda: watcher)
    config_service = _config_service()

    response = await config_api.save_watcher_config(
        WatcherConfigRequest(enabled=True, watch_dirs=[str(alias)]),
        config_service,
    )

    canonical = str(media.resolve())
    desired = watcher.replace_folders.await_args.args[0]
    saved = config_service.save_watcher_config.await_args.args[0]
    assert desired[0].path == canonical
    assert saved.watch_dirs == [canonical]
    assert response.watch_dirs == [canonical]


@pytest.mark.asyncio
async def test_disabling_global_watcher_atomically_disables_folders(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    existing = WatchedFolder(id="folder-1", path="/media/tv", enabled=True)
    watcher = _watcher([existing], running=True)
    monkeypatch.setattr(config_api, "get_watcher_service", lambda: watcher)

    await config_api.save_watcher_config(
        WatcherConfigRequest(enabled=False, watch_dirs=[existing.path]),
        _config_service(WatcherConfig(enabled=True, watch_dirs=[existing.path])),
    )

    desired = watcher.replace_folders.await_args.args[0]
    assert len(desired) == 1 and desired[0].enabled is False
    watcher.stop.assert_awaited_once_with(require_clean=True)
    watcher.start.assert_not_awaited()


@pytest.mark.asyncio
async def test_enabled_empty_watch_list_removes_stale_folders(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    existing = WatchedFolder(id="folder-1", path="/media/tv")
    watcher = _watcher([existing])
    monkeypatch.setattr(config_api, "get_watcher_service", lambda: watcher)

    await config_api.save_watcher_config(
        WatcherConfigRequest(enabled=True, watch_dirs=[]),
        _config_service(),
    )

    watcher.replace_folders.assert_awaited_once_with([])
    watcher.stop.assert_awaited_once_with(require_clean=True)
    watcher.start.assert_not_awaited()


@pytest.mark.asyncio
async def test_p115_resolution_failure_does_not_persist_partial_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    watcher = _watcher([])
    monkeypatch.setattr(config_api, "get_watcher_service", lambda: watcher)
    config_service = _config_service()
    config_service.get_115_config = AsyncMock(
        return_value=SimpleNamespace(is_logged_in=True)
    )

    class FailingP115Service:
        def __init__(self, _config_service):
            pass

        async def _load_p115_client_with_config(self, _config):
            return object()

        def _normalize_virtual_path(self, path):
            return path

        async def _resolve_directory_id(self, **_kwargs):
            raise RuntimeError("remote unavailable")

    monkeypatch.setattr(config_api, "P115Service", FailingP115Service)

    with pytest.raises(HTTPException) as exc_info:
        await config_api.save_watcher_config(
            WatcherConfigRequest(
                enabled=True,
                mode=WatcherMode.EVENT,
                watch_dirs=["/115网盘/电视剧"],
            ),
            config_service,
        )

    assert exc_info.value.status_code == 400
    watcher.stop.assert_not_awaited()
    watcher.replace_folders.assert_not_awaited()
    config_service.save_watcher_config.assert_not_awaited()


@pytest.mark.asyncio
async def test_start_failure_restores_previous_database_and_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    old_path = tmp_path / "old"
    new_path = tmp_path / "new"
    old_path.mkdir()
    new_path.mkdir()
    monkeypatch.setenv("MHTI_ALLOWED_MEDIA_ROOTS", str(tmp_path))
    existing = WatchedFolder(id="old", path=str(old_path), enabled=True)
    watcher = _watcher([existing], running=True)
    watcher.start.side_effect = [RuntimeError("start failed"), None]
    monkeypatch.setattr(config_api, "get_watcher_service", lambda: watcher)
    old_config = WatcherConfig(enabled=True, watch_dirs=[str(old_path)])
    config_service = _config_service(old_config)

    with pytest.raises(HTTPException) as exc_info:
        await config_api.save_watcher_config(
            WatcherConfigRequest(enabled=True, watch_dirs=[str(new_path)]),
            config_service,
        )

    assert exc_info.value.status_code == 500
    assert watcher.replace_folders.await_count == 2
    assert watcher.replace_folders.await_args_list[1].args[0] == [existing]
    assert config_service.save_watcher_config.await_count == 2
    assert config_service.save_watcher_config.await_args_list[1].args[0] == old_config
    assert watcher.start.await_count == 2
