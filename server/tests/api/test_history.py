"""History API regression tests."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from server.api import history as history_api
from server.models.history import ConflictType, HistoryRecordCreate, TaskStatus
from server.services.history_service import HistoryService


@pytest.mark.asyncio
async def test_resolve_conflict_allows_reprocessing_skipped_record(monkeypatch):
    """A skipped conflict can be reopened with the original conflict context."""
    record = SimpleNamespace(
        status=TaskStatus.SKIPPED,
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
async def test_retry_allows_rematching_skipped_record(monkeypatch):
    """A skipped file conflict can be retried with a new TMDB match."""
    record = SimpleNamespace(
        status=TaskStatus.SKIPPED,
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
