"""Watcher-service regression tests for the post-refactor P1 fixes."""

from datetime import datetime
from types import SimpleNamespace

import pytest

from server.application.watcher_service import (
    PendingFile,
    P115EventStrategy,
    P115ScanStrategy,
    WatcherService,
)
from server.models.organize import OrganizeMode
from server.models.watcher import DetectedFile, WatchedFolder, WatcherMode


def _local_folder(path: str) -> WatchedFolder:
    return WatchedFolder(
        id="local-folder",
        path=path,
        mode=WatcherMode.COMPAT,
        file_stable_seconds=0,
    )


@pytest.mark.asyncio
async def test_pending_files_stay_queued_until_job_handoff_succeeds(
    temp_db, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    completed_path = tmp_path / "completed.mkv"
    failed_path = tmp_path / "retry.mkv"
    completed_path.write_bytes(b"completed")
    failed_path.write_bytes(b"retry")

    service = WatcherService(db_path=temp_db)
    folder = _local_folder(str(tmp_path))
    service._pending_files[str(completed_path)] = PendingFile(
        path=str(completed_path),
        detected_at=0,
        folder=folder,
    )
    service._pending_files[str(failed_path)] = PendingFile(
        path=str(failed_path),
        detected_at=0,
        folder=folder,
    )

    async def fake_create_jobs(
        files: list[DetectedFile], _folder: WatchedFolder
    ) -> set[str]:
        assert {file.path for file in files} == {
            str(completed_path),
            str(failed_path),
        }
        return {str(completed_path)}

    monkeypatch.setattr(service, "_create_jobs_for_files", fake_create_jobs)

    await service._process_pending_once()

    assert str(completed_path) not in service._pending_files
    assert str(failed_path) in service._pending_files


@pytest.mark.asyncio
async def test_115_watcher_authorizes_local_output_and_normalizes_link_mode(
    temp_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    created_jobs = []

    class FakeConfigService:
        async def get_organize_config(self):
            return SimpleNamespace(
                organize_dir="/library",
                metadata_dir="",
                organize_mode=OrganizeMode.HARDLINK,
            )

    class FakeScrapeJobService:
        async def create_job(self, job):
            created_jobs.append(job)
            return object()

    monkeypatch.setattr(
        "server.domain.system.config_service.ConfigService", FakeConfigService
    )
    monkeypatch.setattr(
        "server.application.scrape_job_service.ScrapeJobService",
        FakeScrapeJobService,
    )

    folder = WatchedFolder(
        id="p115-folder",
        path="/115网盘/待整理",
        provider="115",
        mode=WatcherMode.COMPAT,
        file_id="folder-1",
    )
    file = DetectedFile(
        path="/115网盘/待整理/episode.mkv",
        detected_at=datetime.now(),
        file_size=1,
        stable=True,
        file_id="file-1",
        parent_id="folder-1",
    )

    completed = await WatcherService(db_path=temp_db)._create_jobs_for_files(
        [file], folder
    )

    assert completed == {file.path}
    assert len(created_jobs) == 1
    job = created_jobs[0]
    assert job.allow_local_output is True
    assert job.link_mode == OrganizeMode.COPY
    assert job.file_locator is not None
    assert job.file_locator.file_id == "file-1"


def test_legacy_watcher_rows_infer_provider_and_safe_mode(temp_db) -> None:
    service = WatcherService(db_path=temp_db)
    legacy_row = {
        "id": "legacy-115",
        "path": "/115网盘/待整理",
        "enabled": 1,
        "mode": "realtime",
        "scan_interval_seconds": 60,
        "file_stable_seconds": 30,
        "auto_scrape": 1,
        "output_dir": None,
        # This is the default inserted when an old database gains the column.
        "provider": "local",
        "file_id": "folder-1",
        "last_scan": None,
        "created_at": None,
    }

    folder = service._row_to_folder(legacy_row)

    assert folder.provider == "115"
    assert folder.mode == WatcherMode.COMPAT


def test_legacy_local_watcher_row_repairs_conflicting_values(temp_db) -> None:
    service = WatcherService(db_path=temp_db)
    legacy_row = {
        "id": "legacy-local",
        "path": "/media/incoming",
        "enabled": 1,
        "mode": "event",
        "scan_interval_seconds": 60,
        "file_stable_seconds": 30,
        "auto_scrape": 1,
        "output_dir": "/115网盘/媒体库",
        "provider": "115",
        "file_id": None,
        "last_scan": None,
        "created_at": None,
    }

    folder = service._row_to_folder(legacy_row)

    assert folder.provider == "local"
    assert folder.mode == WatcherMode.COMPAT
    assert folder.output_dir is None


def test_115_detection_keeps_locator_metadata_until_handoff(temp_db) -> None:
    service = WatcherService(db_path=temp_db)
    folder = WatchedFolder(
        id="p115-folder",
        path="/115网盘/待整理",
        provider="115",
        mode=WatcherMode.COMPAT,
        file_id="folder-1",
    )
    strategy = P115ScanStrategy(folder, lambda _path, _folder: None)
    path = "/115网盘/待整理/episode.mkv"
    strategy.detected_meta[path] = {
        "file_id": "file-1",
        "parent_id": "folder-1",
        "size": 123,
    }
    service._strategies[folder.id] = strategy

    service._on_file_detected(path, folder)

    assert service._pending_files[path].file_id == "file-1"
    assert strategy.detected_meta[path]["file_id"] == "file-1"


@pytest.mark.asyncio
async def test_115_event_directory_collection_paginates_and_has_no_depth_limit() -> None:
    folder = WatchedFolder(
        id="event-folder",
        path="/115网盘/待整理",
        provider="115",
        mode=WatcherMode.EVENT,
        file_id="root",
    )
    strategy = P115EventStrategy(folder, lambda _path, _folder: None)

    class FakeBrowseService:
        async def browse(self, *, path, file_id, page, page_size):
            if file_id == "root" and page == 1:
                entries = [
                    {"is_dir": False, "file_id": f"file-{index}"}
                    for index in range(99)
                ]
                entries.append(
                    {"is_dir": True, "file_id": "child-page-1", "path": f"{path}/p1"}
                )
                return {"entries": entries, "total": 101}
            if file_id == "root" and page == 2:
                return {
                    "entries": [
                        {
                            "is_dir": True,
                            "file_id": "child-page-2",
                            "path": f"{path}/p2",
                        }
                    ],
                    "total": 101,
                }
            if file_id == "child-page-1":
                return {
                    "entries": [
                        {
                            "is_dir": True,
                            "file_id": "deep-child",
                            "path": f"{path}/deep",
                        }
                    ],
                    "total": 1,
                }
            return {"entries": [], "total": 0}

    await strategy._collect_subdir_ids(FakeBrowseService(), folder.path, folder.file_id)

    assert {"child-page-1", "child-page-2", "deep-child"}.issubset(
        strategy._watched_dir_ids
    )
