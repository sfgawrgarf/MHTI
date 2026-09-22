"""Watcher lifecycle and background-error regressions."""

import asyncio
import logging
import time
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import aiosqlite

from server.core.db import configure_connection, create_all_tables

from server.models.watcher import (
    DetectedFile,
    WatchedFolder,
    WatchedFolderCreate,
    WatchedFolderUpdate,
    WatcherMode,
    WatcherStatus,
)
from server.services import watcher_service as watcher_module
from server.services.watcher_service import (
    PendingFile,
    P115EventStrategy,
    P115ScanStrategy,
    WatcherService,
)


class FakeStrategy:
    def __init__(self, folder: WatchedFolder) -> None:
        self.folder = folder
        self.stopped = False

    async def stop(self) -> None:
        self.stopped = True


class FailingStopStrategy(FakeStrategy):
    async def stop(self) -> None:
        raise RuntimeError("strategy stop failed")


def _p115_folder(*, mode: WatcherMode = WatcherMode.COMPAT) -> WatchedFolder:
    return WatchedFolder(
        id="p115-folder",
        path="/115网盘/待整理",
        provider="115",
        mode=mode,
        file_id="folder-1",
        file_stable_seconds=0,
    )


def test_p115_scan_recovery_establishes_baseline_before_emitting_new_files() -> None:
    detected: list[str] = []
    folder = _p115_folder()
    strategy = P115ScanStrategy(folder, lambda path, _folder: detected.append(path))

    strategy._apply_scan_snapshot([
        {"file_id": "old-1", "path": "/115网盘/待整理/old.mkv"},
    ])
    assert detected == []

    strategy._apply_scan_snapshot([
        {"file_id": "old-1", "path": "/115网盘/待整理/old.mkv"},
        {
            "file_id": "new-1",
            "parent_id": "folder-1",
            "path": "/115网盘/待整理/new.mkv",
            "size": 123,
        },
    ])

    assert detected == ["/115网盘/待整理/new.mkv"]
    assert strategy.detected_meta[detected[0]]["file_id"] == "new-1"


def test_p115_event_strategy_fails_closed_without_scope_or_file_id() -> None:
    detected: list[str] = []
    strategy = P115EventStrategy(
        _p115_folder(mode=WatcherMode.EVENT),
        lambda path, _folder: detected.append(path),
    )
    valid_item = {
        "file_id": "file-1",
        "file_name": "episode.mkv",
        "ico": "mkv",
        "parent_id": "folder-1",
    }

    strategy._process_event_item(valid_item)
    assert detected == []

    strategy._watched_dir_ids.add("folder-1")
    strategy._process_event_item({**valid_item, "file_id": None})
    assert detected == []

    strategy._process_event_item(valid_item)
    assert detected == ["/115网盘/待整理/episode.mkv"]


def test_p115_event_strategy_accepts_uppercase_strm_extension() -> None:
    detected: list[str] = []
    strategy = P115EventStrategy(
        _p115_folder(mode=WatcherMode.EVENT),
        lambda path, _folder: detected.append(path),
    )
    strategy._watched_dir_ids.add("folder-1")

    assert strategy._process_event_item(
        {
            "file_id": "file-strm",
            "file_name": "episode.STRM",
            "ico": "STRM",
            "parent_id": "folder-1",
        }
    )
    assert detected == ["/115网盘/待整理/episode.STRM"]


def test_p115_event_deduplication_cache_is_bounded() -> None:
    strategy = P115EventStrategy(
        _p115_folder(mode=WatcherMode.EVENT),
        lambda _path, _folder: None,
    )
    strategy.PROCESSED_FILE_LIMIT = 2

    strategy._remember_processed_file_id("one")
    strategy._remember_processed_file_id("two")
    strategy._remember_processed_file_id("three")

    assert strategy._processed_file_ids == {"two", "three"}


@pytest.mark.asyncio
async def test_p115_event_empty_baseline_starts_from_current_time(
    monkeypatch,
) -> None:
    strategy = P115EventStrategy(
        _p115_folder(mode=WatcherMode.EVENT),
        lambda _path, _folder: None,
    )

    async def collect_scope() -> None:
        strategy._watched_dir_ids = {"folder-1"}

    client = SimpleNamespace(
        life_list=AsyncMock(return_value={"state": True, "data": {"list": []}}),
    )
    monkeypatch.setattr(strategy, "_collect_dir_ids", collect_scope)
    monkeypatch.setattr(strategy, "_get_client", AsyncMock(return_value=client))
    monkeypatch.setattr(watcher_module.time, "time", lambda: 1234.9)

    await strategy._init()

    assert strategy._last_update_time == 1234


@pytest.mark.asyncio
async def test_p115_event_scope_is_paginated_and_has_no_depth_five_cutoff() -> None:
    strategy = P115EventStrategy(
        _p115_folder(mode=WatcherMode.EVENT),
        lambda _path, _folder: None,
    )

    async def browse(*, path, file_id, page, page_size):
        if file_id == "folder-1" and page == 1:
            return {
                "current_file_id": "folder-1",
                "entries": [
                    {"is_dir": False, "path": f"{path}/file-{index}.txt"}
                    for index in range(page_size)
                ],
                "total": page_size + 1,
            }
        if file_id == "folder-1" and page == 2:
            return {
                "current_file_id": "folder-1",
                "entries": [
                    {
                        "is_dir": True,
                        "file_id": "level-1",
                        "path": f"{path}/level-1",
                    }
                ],
                "total": page_size + 1,
            }
        if str(file_id).startswith("level-"):
            level = int(str(file_id).split("-")[1])
            child = level + 1
            entries = []
            if child <= 7:
                entries.append(
                    {
                        "is_dir": True,
                        "file_id": f"level-{child}",
                        "path": f"{path}/level-{child}",
                    }
                )
            return {
                "current_file_id": file_id,
                "entries": entries,
                "total": len(entries),
            }
        raise AssertionError((path, file_id, page))

    await strategy._collect_subdir_ids(
        SimpleNamespace(browse=browse),
        strategy.folder.path,
        strategy.folder.file_id,
        strategy._watched_dir_paths,
    )

    assert "level-7" in strategy._watched_dir_paths
    assert strategy._watched_dir_paths["level-7"].endswith("/level-7")


def test_p115_event_uses_full_nested_directory_path() -> None:
    detected: list[str] = []
    strategy = P115EventStrategy(
        _p115_folder(mode=WatcherMode.EVENT),
        lambda path, _folder: detected.append(path),
    )
    strategy._watched_dir_ids = {"nested-id"}
    strategy._watched_dir_paths = {
        "nested-id": "/115网盘/待整理/Anime/Season 1",
    }

    assert strategy._process_event_item(
        {
            "file_id": "episode-1",
            "file_name": "episode.mkv",
            "ico": "mkv",
            "parent_id": "nested-id",
        }
    )

    assert detected == ["/115网盘/待整理/Anime/Season 1/episode.mkv"]


@pytest.mark.asyncio
async def test_pending_p115_identity_survives_strategy_replacement(
    temp_db, monkeypatch
) -> None:
    service = WatcherService(temp_db)
    folder = _p115_folder(mode=WatcherMode.EVENT)
    path = f"{folder.path}/episode.mkv"
    old_strategy = P115EventStrategy(folder, service._on_file_detected)
    old_strategy.detected_meta[path] = {
        "file_id": "episode-id",
        "parent_id": "folder-1",
        "size": 123,
    }
    service._strategies[folder.id] = old_strategy
    service._on_file_detected(path, folder)

    service._strategies[folder.id] = P115EventStrategy(
        folder, service._on_file_detected
    )
    # A duplicate callback after restart has no strategy-local metadata. It must
    # not overwrite the durable pending identity captured by the old strategy.
    service._on_file_detected(path, folder)
    captured: list[DetectedFile] = []

    async def create_jobs(files, _folder):
        captured.extend(files)
        return {file.path for file in files}

    monkeypatch.setattr(service, "_create_jobs_for_files", create_jobs)

    await service._process_pending_once()

    assert len(captured) == 1
    assert captured[0].file_id == "episode-id"
    assert captured[0].parent_id == "folder-1"


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"path": "/115网盘/待整理", "provider": "local"}, "不能声明为本地"),
        ({"path": "/media", "provider": "115"}, "必须使用 /115网盘"),
        (
            {
                "path": "/media",
                "provider": "local",
                "mode": WatcherMode.EVENT,
            },
            "不支持 115 事件模式",
        ),
        (
            {
                "path": "/media",
                "provider": "local",
                "output_dir": "/115网盘/媒体库",
            },
            "暂不支持将本地监控文件输出到 115",
        ),
    ],
)
def test_watched_folder_create_rejects_inconsistent_storage_selection(
    kwargs,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        WatchedFolderCreate(**kwargs)


@pytest.mark.parametrize(
    "field,value",
    [
        ("scan_interval_seconds", 4),
        ("scan_interval_seconds", 86401),
        ("file_stable_seconds", -1),
        ("file_stable_seconds", 86401),
    ],
)
def test_watcher_timing_limits_reject_pathological_values(field, value) -> None:
    with pytest.raises(ValueError):
        WatchedFolderCreate(path="/media", **{field: value})


@pytest.mark.asyncio
async def test_explicit_null_clears_optional_folder_fields(temp_db) -> None:
    async with aiosqlite.connect(temp_db) as db:
        await configure_connection(db)
        await create_all_tables(db)
        await db.commit()
    service = WatcherService(temp_db)
    folder = await service.create_folder(
        WatchedFolderCreate(
            path="/media/source",
            output_dir="/media/output",
            file_id="legacy-id",
        )
    )

    updated = await service.update_folder(
        folder.id,
        WatchedFolderUpdate(output_dir=None, file_id=None),
    )

    assert updated is not None
    assert updated.output_dir is None
    assert updated.file_id is None


@pytest.mark.asyncio
async def test_create_folder_does_not_persist_when_runtime_start_fails(
    temp_db, monkeypatch
) -> None:
    async with aiosqlite.connect(temp_db) as db:
        await configure_connection(db)
        await create_all_tables(db)
        await db.commit()
    service = WatcherService(temp_db)
    service._running = True
    monkeypatch.setattr(
        service,
        "_start_folder_watch",
        AsyncMock(side_effect=RuntimeError("start failed")),
    )

    with pytest.raises(RuntimeError, match="start failed"):
        await service.create_folder(WatchedFolderCreate(path="/media/new"))

    folders, total = await service.list_folders()
    assert folders == []
    assert total == 0


@pytest.mark.asyncio
async def test_update_folder_restores_runtime_and_database_when_restart_fails(
    temp_db, monkeypatch
) -> None:
    async with aiosqlite.connect(temp_db) as db:
        await configure_connection(db)
        await create_all_tables(db)
        await db.commit()
    service = WatcherService(temp_db)
    folder = await service.create_folder(WatchedFolderCreate(path="/media/original"))
    service._running = True
    restart = AsyncMock(side_effect=[RuntimeError("restart failed"), None])
    monkeypatch.setattr(service, "_restart_folder_watch", restart)

    with pytest.raises(RuntimeError, match="restart failed"):
        await service.update_folder(
            folder.id,
            WatchedFolderUpdate(path="/media/replacement"),
        )

    persisted = await service.get_folder(folder.id)
    assert persisted is not None
    assert persisted.path == "/media/original"
    assert restart.await_count == 2
    assert restart.await_args_list[-1].args[0].path == "/media/original"


@pytest.mark.asyncio
async def test_delete_folder_restores_runtime_when_database_delete_fails(
    temp_db, monkeypatch
) -> None:
    async with aiosqlite.connect(temp_db) as db:
        await configure_connection(db)
        await create_all_tables(db)
        await db.commit()
    service = WatcherService(temp_db)
    folder = await service.create_folder(WatchedFolderCreate(path="/media/original"))
    service._running = True
    service._strategies[folder.id] = FakeStrategy(folder)

    async with aiosqlite.connect(temp_db) as db:
        await db.execute(
            f"""CREATE TRIGGER reject_watcher_delete
                BEFORE DELETE ON watched_folders
                WHEN OLD.id = '{folder.id}'
                BEGIN SELECT RAISE(ABORT, 'delete blocked'); END"""
        )
        await db.commit()

    async def restore(folder_to_restore: WatchedFolder) -> None:
        service._strategies[folder_to_restore.id] = FakeStrategy(folder_to_restore)

    monkeypatch.setattr(service, "_start_folder_watch", restore)

    with pytest.raises(aiosqlite.IntegrityError, match="delete blocked"):
        await service.delete_folder(folder.id)

    assert await service.get_folder(folder.id) is not None
    assert folder.id in service._strategies


@pytest.mark.asyncio
async def test_delete_missing_folder_cleans_stale_runtime_state(temp_db) -> None:
    async with aiosqlite.connect(temp_db) as db:
        await configure_connection(db)
        await create_all_tables(db)
        await db.commit()
    service = WatcherService(temp_db)
    stale = WatchedFolder(id="stale", path="/media/stale")
    strategy = FakeStrategy(stale)
    service._strategies[stale.id] = strategy
    service._pending_files["/media/stale/episode.mkv"] = PendingFile(
        path="/media/stale/episode.mkv",
        detected_at=0,
        folder=stale,
    )

    deleted = await service.delete_folder(stale.id)

    assert deleted is False
    assert strategy.stopped is True
    assert stale.id not in service._strategies
    assert service._pending_files == {}


@pytest.mark.asyncio
async def test_legacy_watcher_intervals_are_normalized_before_loading(temp_db) -> None:
    async with aiosqlite.connect(temp_db) as db:
        await configure_connection(db)
        await create_all_tables(db)
        await db.execute(
            """INSERT INTO watched_folders
               (id, path, scan_interval_seconds, file_stable_seconds, created_at)
               VALUES ('legacy-low', '/media/low', 1, -2, '2026-01-01')"""
        )
        await db.execute(
            """INSERT INTO watched_folders
               (id, path, scan_interval_seconds, file_stable_seconds, created_at)
               VALUES ('legacy-high', '/media/high', 999999, 999999, '2026-01-01')"""
        )
        await db.execute(
            """INSERT INTO watched_folders
               (id, path, scan_interval_seconds, file_stable_seconds, created_at)
               VALUES ('legacy-types', '/media/types', 'invalid', 5.5, '2026-01-01')"""
        )
        await db.commit()

    folders, total = await WatcherService(temp_db).list_folders()
    by_id = {folder.id: folder for folder in folders}

    assert total == 3
    assert by_id["legacy-low"].scan_interval_seconds == 5
    assert by_id["legacy-low"].file_stable_seconds == 0
    assert by_id["legacy-high"].scan_interval_seconds == 86400
    assert by_id["legacy-high"].file_stable_seconds == 86400
    assert by_id["legacy-types"].scan_interval_seconds == 60
    assert by_id["legacy-types"].file_stable_seconds == 5


@pytest.mark.asyncio
async def test_pending_file_is_removed_only_after_job_creation_succeeds(
    temp_db,
    tmp_path,
    monkeypatch,
) -> None:
    service = WatcherService(temp_db)
    folder = WatchedFolder(
        id="local-folder",
        path=str(tmp_path),
        file_stable_seconds=0,
    )
    first = tmp_path / "first.mkv"
    second = tmp_path / "second.mkv"
    first.touch()
    second.touch()
    detected_at = time.time() - 10
    service._pending_files = {
        str(first): PendingFile(str(first), detected_at, folder),
        str(second): PendingFile(str(second), detected_at, folder),
    }
    monkeypatch.setattr(
        service,
        "_create_jobs_for_files",
        AsyncMock(return_value={str(first)}),
    )

    await service._process_pending_once()

    assert str(first) not in service._pending_files
    assert str(second) in service._pending_files


@pytest.mark.asyncio
async def test_job_creation_failure_is_isolated_per_detected_file(
    temp_db,
    monkeypatch,
) -> None:
    service = WatcherService(temp_db)
    folder = WatchedFolder(id="local-folder", path="/media")
    files = [
        DetectedFile(
            path=f"/media/{name}.mkv",
            detected_at=datetime.now(),
            file_size=1,
            stable=True,
        )
        for name in ("first", "second")
    ]
    config = SimpleNamespace(
        organize_dir="/library",
        metadata_dir="",
        organize_mode=watcher_module.OrganizeMode.COPY,
    )
    config_service = SimpleNamespace(
        get_organize_config=AsyncMock(return_value=config),
    )
    create_job = AsyncMock(side_effect=[RuntimeError("temporary failure"), object()])

    monkeypatch.setattr(
        "server.services.config_service.ConfigService",
        lambda: config_service,
    )
    monkeypatch.setattr(
        "server.services.scrape_job_service.ScrapeJobService",
        lambda: SimpleNamespace(create_job=create_job),
    )

    completed = await service._create_jobs_for_files(files, folder)

    assert completed == {"/media/second.mkv"}
    assert create_job.await_count == 2


def test_legacy_watcher_row_is_safely_normalized(temp_db) -> None:
    service = WatcherService(temp_db)
    folder = service._row_to_folder({
        "id": "legacy",
        "path": "/115网盘/待整理",
        "enabled": 1,
        "mode": "realtime",
        "scan_interval_seconds": 60,
        "file_stable_seconds": 30,
        "auto_scrape": 1,
        "output_dir": None,
        "provider": "local",
        "file_id": "folder-1",
        "last_scan": None,
        "created_at": None,
    })

    assert folder.provider == "115"
    assert folder.mode == WatcherMode.COMPAT


@pytest.mark.asyncio
async def test_failed_start_rolls_back_partial_watcher_state(
    temp_db, monkeypatch
) -> None:
    service = WatcherService(temp_db)
    first_path = temp_db.parent / "first"
    second_path = temp_db.parent / "second"
    first_path.mkdir()
    second_path.mkdir()
    first = WatchedFolder(id="first", path=str(first_path))
    second = WatchedFolder(id="second", path=str(second_path))
    strategy = FakeStrategy(first)
    monkeypatch.setattr(
        service,
        "list_folders",
        AsyncMock(return_value=([first, second], 2)),
    )

    async def start_folder(folder: WatchedFolder) -> None:
        if folder.id == first.id:
            service._strategies[folder.id] = strategy
            return
        raise RuntimeError("strategy startup failed")

    monkeypatch.setattr(service, "_start_folder_watch", start_folder)

    with pytest.raises(RuntimeError, match="strategy startup failed"):
        await service.start()

    assert strategy.stopped is True
    assert service._running is False
    assert service._status == WatcherStatus.STOPPED
    assert service._strategies == {}
    assert service._process_task is None
    assert service._initial_scan_task is None


@pytest.mark.asyncio
async def test_concurrent_start_is_idempotent_and_stop_clears_tasks(
    temp_db, monkeypatch
) -> None:
    service = WatcherService(temp_db)
    list_folders = AsyncMock(return_value=([], 0))
    monkeypatch.setattr(service, "list_folders", list_folders)

    initial_scan_started = asyncio.Event()

    async def initial_scan(_folders) -> None:
        initial_scan_started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(service, "_initial_scan", initial_scan)

    await asyncio.gather(service.start(), service.start())
    await initial_scan_started.wait()
    process_task = service._process_task
    initial_task = service._initial_scan_task

    assert list_folders.await_count == 1
    assert service._running is True
    assert service._status == WatcherStatus.RUNNING
    assert process_task is not None
    assert initial_task is not None

    await service.stop()

    assert process_task.done()
    assert initial_task.cancelled()
    assert service._process_task is None
    assert service._initial_scan_task is None
    assert service._running is False
    assert service._status == WatcherStatus.STOPPED


@pytest.mark.asyncio
async def test_background_task_failure_is_logged(caplog) -> None:
    async def fail() -> None:
        raise RuntimeError("background failed")

    with caplog.at_level(logging.ERROR):
        task = watcher_module._create_background_task(fail(), "watcher-test-task")
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.sleep(0)

    assert "watcher-test-task stopped unexpectedly" in caplog.text
    assert "background failed" in caplog.text


@pytest.mark.asyncio
async def test_failed_strategy_stop_remains_visible_for_retry(temp_db, caplog) -> None:
    service = WatcherService(temp_db)
    folder = WatchedFolder(id="stuck", path="/stuck")
    strategy = FailingStopStrategy(folder)
    service._running = True
    service._status = WatcherStatus.RUNNING
    service._strategies[folder.id] = strategy

    with caplog.at_level(logging.ERROR):
        await service.stop()

    assert service._running is False
    assert service._status == WatcherStatus.ERROR
    assert service._strategies == {folder.id: strategy}
    assert "strategy stop failed" in caplog.text

    with pytest.raises(RuntimeError, match="拒绝重复启动"):
        await service.start()

    with pytest.raises(RuntimeError, match="未能停止"):
        await service.stop(require_clean=True)
