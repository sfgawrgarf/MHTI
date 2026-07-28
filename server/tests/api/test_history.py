"""History API regression tests."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import aiosqlite
import pytest
from fastapi import HTTPException

from server.api import history as history_api
from server.core.db import configure_connection, create_all_tables
from server.models.history import ConflictType, HistoryRecordCreate, TaskStatus
from server.models.scraper import ScrapeByIdRequest, ScrapeResult, ScrapeStatus
from server.services.history_service import HistoryService


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [TaskStatus.SKIPPED, TaskStatus.DELETED])
async def test_resolve_conflict_allows_reprocessing_skipped_or_deleted_record(monkeypatch, status):
    """Skipped and deleted conflicts can be reopened with the original context."""
    record = SimpleNamespace(
        status=status,
        conflict_type=ConflictType.FILE_CONFLICT,
        conflict_data={
            "tmdb_id": 123,
            "season": 1,
            "episode": 2,
            "output_dir": "/output",
            "metadata_dir": "/metadata",
            "link_mode": "copy",
        },
        folder_path="/incoming/example.mkv",
    )
    history_service = AsyncMock()
    history_service.get_record.return_value = record
    execute_scrape = AsyncMock(return_value={"success": True})

    monkeypatch.setattr(
        history_api,
        "_restore_locators_from_scrape_job",
        AsyncMock(
            return_value={
                "file_locator": {
                    "provider": "115",
                    "path": "/incoming/example.mkv",
                    "file_id": "abc",
                    "is_dir": False,
                }
            }
        ),
    )
    monkeypatch.setattr(history_api, "_execute_scrape_and_update", execute_scrape)

    result = await history_api.resolve_conflict(
        "record-1",
        history_api.ResolveConflictRequest(
            conflict_type=ConflictType.FILE_CONFLICT,
            file_action="rename",
        ),
        history_service,
    )

    assert result == {"success": True}
    assert execute_scrape.await_count == 1
    scrape_request = execute_scrape.await_args.args[2]
    assert scrape_request.file_path == "/incoming/example.mkv"
    assert scrape_request.tmdb_id == 123
    assert scrape_request.season == 1
    assert scrape_request.episode == 2
    assert scrape_request.file_locator.file_id == "abc"


@pytest.mark.asyncio
async def test_resolve_conflict_rejects_completed_record():
    """Only pending and skipped conflicts can be resolved."""
    record = SimpleNamespace(
        status=TaskStatus.SUCCESS,
        conflict_type=ConflictType.FILE_CONFLICT,
        conflict_data={},
    )
    history_service = AsyncMock()
    history_service.get_record.return_value = record

    with pytest.raises(HTTPException, match="该记录不需要处理") as error:
        await history_api.resolve_conflict(
            "record-1",
            history_api.ResolveConflictRequest(
                conflict_type=ConflictType.FILE_CONFLICT,
                file_action="rename",
            ),
            history_service,
        )

    assert error.value.status_code == 400


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status",
    [TaskStatus.PENDING_ACTION, TaskStatus.SKIPPED, TaskStatus.DELETED],
)
async def test_resolve_conflict_rematches_file_conflict(monkeypatch, status):
    """Eligible file conflicts rematch through resolve, not the retry endpoint."""
    record = SimpleNamespace(
        status=status,
        conflict_type=ConflictType.FILE_CONFLICT,
        conflict_data={
            "tmdb_id": 123,
            "season": 1,
            "episode": 2,
            "output_dir": "/output",
            "metadata_dir": "/metadata",
            "link_mode": "copy",
        },
        folder_path="/incoming/example.mkv",
    )
    history_service = AsyncMock()
    history_service.get_record.return_value = record
    execute_scrape = AsyncMock(return_value={"success": True})

    monkeypatch.setattr(
        history_api,
        "_restore_locators_from_scrape_job",
        AsyncMock(
            return_value={
                "file_locator": {
                    "provider": "115",
                    "path": "/incoming/example.mkv",
                    "file_id": "abc",
                    "is_dir": False,
                }
            }
        ),
    )
    monkeypatch.setattr(history_api, "_execute_scrape_and_update", execute_scrape)

    result = await history_api.resolve_conflict(
        "record-1",
        history_api.ResolveConflictRequest(
            conflict_type=ConflictType.FILE_CONFLICT,
            resolution_action="rematch",
            tmdb_id=456,
            season=2,
            episode=3,
        ),
        history_service,
    )

    assert result == {"success": True}
    scrape_request = execute_scrape.await_args.args[2]
    assert (scrape_request.tmdb_id, scrape_request.season, scrape_request.episode) == (456, 2, 3)
    assert scrape_request.file_locator.file_id == "abc"


@pytest.mark.asyncio
async def test_retry_rejects_pending_action_record():
    """Pending conflicts must be resolved rather than sent to the retry endpoint."""
    record = SimpleNamespace(status=TaskStatus.PENDING_ACTION)
    history_service = AsyncMock()
    history_service.get_record.return_value = record

    with pytest.raises(HTTPException, match="不支持重试") as error:
        await history_api.retry_scrape(
            "record-1",
            history_api.RetryRequest(tmdb_id=456, season=2, episode=3),
            history_service,
        )

    assert error.value.status_code == 400


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [TaskStatus.SKIPPED, TaskStatus.DELETED])
async def test_retry_allows_rematching_skipped_or_deleted_record(monkeypatch, status):
    """Skipped and deleted records can be retried with a new TMDB match."""
    record = SimpleNamespace(
        status=status,
        conflict_data={"output_dir": "/output", "metadata_dir": "/metadata"},
        folder_path="/incoming/example.mkv",
    )
    history_service = AsyncMock()
    history_service.get_record.return_value = record
    execute_scrape = AsyncMock(return_value={"success": True})

    monkeypatch.setattr(history_api, "_restore_locators_from_scrape_job", AsyncMock(return_value={}))
    monkeypatch.setattr(history_api, "_execute_scrape_and_update", execute_scrape)

    result = await history_api.retry_scrape(
        "record-1",
        history_api.RetryRequest(tmdb_id=456, season=2, episode=3),
        history_service,
    )

    assert result == {"success": True}
    scrape_request = execute_scrape.await_args.args[2]
    assert (scrape_request.tmdb_id, scrape_request.season, scrape_request.episode) == (456, 2, 3)


@pytest.mark.asyncio
async def test_delete_record_marks_history_as_deleted_and_allows_rescrape(temp_db):
    """Deleting a record keeps it visible under the deleted status."""
    async with aiosqlite.connect(temp_db) as db:
        await configure_connection(db)
        await create_all_tables(db)
        await db.commit()

    service = HistoryService(db_path=temp_db)
    record = await service.create_record(
        HistoryRecordCreate(
            task_name="test-delete",
            folder_path="/incoming/example.mkv",
            status=TaskStatus.SUCCESS,
            total_files=1,
            success_count=1,
            failed_count=0,
            duration_seconds=1,
            file_fingerprint="fingerprint-1",
        )
    )

    assert await service.delete_record(record.id) is True

    deleted_record = await service.get_record(record.id)
    assert deleted_record is not None
    assert deleted_record.status == TaskStatus.DELETED
    assert deleted_record.error_message == "用户删除"

    records, total = await service.list_records(status=TaskStatus.DELETED)
    assert total == 1
    assert [item.id for item in records] == [record.id]
    assert await service.get_existing_fingerprints(["fingerprint-1"]) == set()


@pytest.mark.asyncio
async def test_success_rematch_queues_replacement_without_replacing_original(monkeypatch, tmp_path):
    """A successful record becomes replaced only in the worker's success path."""
    current_file = tmp_path / "Episode.strm"
    current_file.write_text("https://example.invalid/video", encoding="utf-8")
    record = SimpleNamespace(
        id="history-old",
        status=TaskStatus.SUCCESS,
        scrape_job_id="job-old",
        folder_path=f"/incoming/episode.mkv => {current_file}",
    )
    old_job = SimpleNamespace(
        id="job-old",
        output_dir="/library",
        metadata_dir="/library",
        output_locator=None,
        metadata_locator=None,
        allow_local_output=True,
        link_mode=None,
        source_id=7,
        advanced_settings=None,
    )
    queued_job = SimpleNamespace(id="job-new")
    jobs = AsyncMock()
    jobs.get_job.return_value = old_job
    jobs.create_job.return_value = queued_job
    history_service = AsyncMock()
    history_service.get_record.return_value = record

    monkeypatch.setattr("server.services.scrape_job_service.ScrapeJobService", lambda: jobs)

    result = await history_api.rematch_successful_record(
        "history-old",
        history_api.SuccessRematchRequest(tmdb_id=123, season=2, episode=3),
        history_service,
    )

    assert result["job_id"] == "job-new"
    create_request = jobs.create_job.await_args.args[0]
    assert create_request.file_path == str(current_file)
    assert (create_request.correction_tmdb_id, create_request.correction_season, create_request.correction_episode) == (123, 2, 3)
    assert create_request.correction_history_id == "history-old"
    history_service.update_record.assert_not_awaited()


@pytest.mark.asyncio
async def test_success_rematch_rejects_missing_current_output():
    record = SimpleNamespace(
        id="history-old",
        status=TaskStatus.SUCCESS,
        scrape_job_id="job-old",
        folder_path="/incoming/episode.mkv => /missing/Episode.strm",
    )
    history_service = AsyncMock()
    history_service.get_record.return_value = record

    with pytest.raises(HTTPException, match="当前已整理文件不存在") as error:
        await history_api.rematch_successful_record(
            "history-old",
            history_api.SuccessRematchRequest(tmdb_id=123, season=2, episode=3),
            history_service,
        )

    assert error.value.status_code == 409


@pytest.mark.asyncio
async def test_manual_match_file_conflict_becomes_pending_action(monkeypatch):
    """Manual TMDB matching must retain a destination conflict for user choice."""
    conflict_path = "/library/Show/Season 01/Show - S01E01.strm"
    scraper = SimpleNamespace(
        scrape_by_id=AsyncMock(
            return_value=ScrapeResult(
                file_path="/incoming/example.strm",
                status=ScrapeStatus.FILE_CONFLICT,
                message=f"目标文件已存在: {conflict_path}",
                dest_path=conflict_path,
            )
        )
    )
    monkeypatch.setattr("server.core.container.get_scraper_service", lambda: scraper)
    record = SimpleNamespace(
        scrape_logs=[],
        manual_job_id=None,
        conflict_data={"parsed_title": "example"},
    )
    history_service = AsyncMock()
    history_service.get_record.return_value = record
    history_service.clear_log_cache = Mock()
    request = ScrapeByIdRequest(
        file_path="/incoming/example.strm",
        tmdb_id=123,
        season=1,
        episode=1,
        output_dir="/library",
        metadata_dir="/library",
    )

    result = await history_api._execute_scrape_and_update(
        history_service, "record-1", request, "用户手动输入 TMDB ID"
    )

    assert result["requires_action"] is True
    update = history_service.update_record.await_args
    assert update.kwargs["status"] == TaskStatus.PENDING_ACTION
    assert update.kwargs["conflict_type"] == ConflictType.FILE_CONFLICT
    assert update.kwargs["conflict_data"]["dest_path"] == conflict_path


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["overwrite", "rename"])
async def test_file_conflict_resolution_passes_selected_file_action(monkeypatch, action):
    record = SimpleNamespace(
        status=TaskStatus.PENDING_ACTION,
        conflict_type=ConflictType.FILE_CONFLICT,
        conflict_data={
            "tmdb_id": 123,
            "season": 1,
            "episode": 2,
            "output_dir": "/output",
            "metadata_dir": "/metadata",
            "link_mode": "copy",
        },
        folder_path="/incoming/example.strm",
    )
    history_service = AsyncMock()
    history_service.get_record.return_value = record
    execute_scrape = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(history_api, "_execute_scrape_and_update", execute_scrape)
    monkeypatch.setattr(history_api, "_restore_locators_from_scrape_job", AsyncMock(return_value={}))

    await history_api.resolve_conflict(
        "record-1",
        history_api.ResolveConflictRequest(
            conflict_type=ConflictType.FILE_CONFLICT,
            file_action=action,
        ),
        history_service,
    )

    scrape_request = execute_scrape.await_args.args[2]
    assert scrape_request.file_action == action
