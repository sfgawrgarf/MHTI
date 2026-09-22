"""Watcher API path-boundary regression tests."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from server.api import watcher as watcher_api
from server.models.watcher import (
    WatchedFolder,
    WatchedFolderCreate,
    WatchedFolderUpdate,
)


@pytest.mark.asyncio
async def test_create_folder_rejects_path_outside_allowed_roots(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    monkeypatch.setenv("MHTI_ALLOWED_MEDIA_ROOTS", str(allowed))
    service = SimpleNamespace(create_folder=AsyncMock())

    with pytest.raises(HTTPException) as exc_info:
        await watcher_api.create_folder(
            WatchedFolderCreate(path=str(outside)),
            service,
        )

    assert exc_info.value.status_code == 400
    service.create_folder.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_folder_rejects_regular_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    media_file = tmp_path / "episode.mkv"
    media_file.write_bytes(b"video")
    monkeypatch.setenv("MHTI_ALLOWED_MEDIA_ROOTS", str(tmp_path))
    service = SimpleNamespace(create_folder=AsyncMock())

    with pytest.raises(HTTPException) as exc_info:
        await watcher_api.create_folder(
            WatchedFolderCreate(path=str(media_file)),
            service,
        )

    assert exc_info.value.status_code == 400
    service.create_folder.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_folder_persists_canonical_symlink_target(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    media = tmp_path / "media"
    media.mkdir()
    alias = tmp_path / "media-alias"
    alias.symlink_to(media, target_is_directory=True)
    monkeypatch.setenv("MHTI_ALLOWED_MEDIA_ROOTS", str(tmp_path))

    async def create(validated: WatchedFolderCreate) -> WatchedFolder:
        return WatchedFolder(id="folder-1", **validated.model_dump())

    service = SimpleNamespace(create_folder=AsyncMock(side_effect=create))
    result = await watcher_api.create_folder(
        WatchedFolderCreate(path=str(alias)),
        service,
    )

    validated = service.create_folder.await_args.args[0]
    assert validated.path == str(media.resolve())
    assert result.path == str(media.resolve())


@pytest.mark.asyncio
async def test_update_folder_revalidates_existing_local_path_when_enabling(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    media_file = tmp_path / "episode.mkv"
    media_file.write_bytes(b"video")
    monkeypatch.setenv("MHTI_ALLOWED_MEDIA_ROOTS", str(tmp_path))
    current = WatchedFolder(
        id="folder-1",
        path=str(media_file),
        enabled=False,
    )
    service = SimpleNamespace(
        get_folder=AsyncMock(return_value=current),
        update_folder=AsyncMock(),
    )

    with pytest.raises(HTTPException) as exc_info:
        await watcher_api.update_folder(
            current.id,
            WatchedFolderUpdate(enabled=True),
            service,
        )

    assert exc_info.value.status_code == 400
    service.update_folder.assert_not_awaited()
