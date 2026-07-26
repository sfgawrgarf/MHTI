"""History API regression tests."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from server.api import history as history_api
from server.models.history import ConflictType, TaskStatus


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
