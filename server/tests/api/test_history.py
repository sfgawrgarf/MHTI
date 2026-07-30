"""History API regression tests."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call

import aiosqlite
import pytest
from fastapi import HTTPException

from server.api import history as history_api
from server.core.db import configure_connection, create_all_tables
from server.models.history import ConflictType, HistoryRecordCreate, TaskStatus
from server.models.scraper import ScrapeByIdRequest, ScrapeResult, ScrapeStatus
from server.services.history_service import HistoryService


@pytest.mark.asyncio
async def test_list_all_pending_record_ids_reads_every_backend_page():
    """The all-pending action must not inherit the UI's 20-row page."""
    first_page = [SimpleNamespace(id=f"record-{index}") for index in range(500)]
    second_page = [SimpleNamespace(id=f"record-{index}") for index in range(500, 503)]
    history_service = AsyncMock()
    history_service.list_records.side_effect = [
        (first_page, 503),
        (second_page, 503),
    ]

    record_ids = await history_api._list_all_pending_record_ids(history_service)

    assert record_ids == [f"record-{index}" for index in range(503)]
    assert history_service.list_records.await_args_list == [
        call(limit=500, offset=0, status=TaskStatus.PENDING_ACTION),
        call(limit=500, offset=500, status=TaskStatus.PENDING_ACTION),
    ]


@pytest.mark.asyncio
async def test_ai_retry_all_pending_uses_backend_collection(monkeypatch):
    """The explicit all-pending mode sends every collected ID through validation."""
    collect_ids = AsyncMock(return_value=["record-1", "record-2"])
    monkeypatch.setattr(history_api, "_list_all_pending_record_ids", collect_ids)
    history_service = AsyncMock()
    history_service.get_record.side_effect = [None, None]

    result = await history_api.retry_no_match_with_ai(
        history_api.AIRetryRequest(all_pending=True),
        history_service,
    )

    collect_ids.assert_awaited_once_with(history_service)
    assert [item["id"] for item in result["skipped"]] == ["record-1", "record-2"]
    assert result["queued_job_ids"] == []


@pytest.mark.asyncio
async def test_ai_retry_rejects_ambiguous_all_pending_request():
    history_service = AsyncMock()

    with pytest.raises(HTTPException, match="不能同时使用") as error:
        await history_api.retry_no_match_with_ai(
            history_api.AIRetryRequest(
                record_ids=["record-1"],
                all_pending=True,
            ),
            history_service,
        )

    assert error.value.status_code == 400


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
@pytest.mark.parametrize(
    "conflict_type",
    [
        ConflictType.NEED_SELECTION,
        ConflictType.NEED_SEASON_EPISODE,
        ConflictType.FILE_CONFLICT,
        ConflictType.EMBY_CONFLICT,
    ],
)
async def test_resolve_conflict_rematches_any_selectable_conflict(
    monkeypatch, status, conflict_type
):
    """Eligible conflicts can discard stale TMDB context through resolve."""
    record = SimpleNamespace(
        status=status,
        conflict_type=conflict_type,
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
            conflict_type=conflict_type,
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
@pytest.mark.parametrize(
    ("tmdb_id", "season", "episode", "message"),
    [
        (None, 1, 2, "请选择 TMDB ID"),
        (456, None, 2, "请提供季/集号"),
        (456, 1, None, "请提供季/集号"),
    ],
)
async def test_resolve_conflict_rematch_requires_complete_identity(
    monkeypatch, tmdb_id, season, episode, message
):
    record = SimpleNamespace(
        status=TaskStatus.PENDING_ACTION,
        conflict_type=ConflictType.NEED_SEASON_EPISODE,
        conflict_data={"tmdb_id": 123, "output_dir": "/output"},
    )
    history_service = AsyncMock()
    history_service.get_record.return_value = record
    monkeypatch.setattr(
        history_api,
        "_restore_locators_from_scrape_job",
        AsyncMock(return_value={}),
    )

    with pytest.raises(HTTPException, match=message) as error:
        await history_api.resolve_conflict(
            "record-1",
            history_api.ResolveConflictRequest(
                conflict_type=ConflictType.NEED_SEASON_EPISODE,
                resolution_action="rematch",
                tmdb_id=tmdb_id,
                season=season,
                episode=episode,
            ),
            history_service,
        )

    assert error.value.status_code == 400


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
async def test_update_record_on_success_clears_stale_conflict_context(temp_db):
    """A resolved record must not retain its deleted/error state or old TMDB match."""
    async with aiosqlite.connect(temp_db) as db:
        await configure_connection(db)
        await create_all_tables(db)
        await db.commit()

    service = HistoryService(db_path=temp_db)
    record = await service.create_record(
        HistoryRecordCreate(
            task_name="test-resolve-success",
            folder_path="/incoming/example.strm",
            status=TaskStatus.DELETED,
            total_files=1,
            success_count=0,
            failed_count=0,
            duration_seconds=0,
            error_message="用户删除",
            conflict_type=ConflictType.NEED_SEASON_EPISODE,
            conflict_data={"tmdb_id": 123},
        )
    )
    async with aiosqlite.connect(temp_db) as db:
        await configure_connection(db)
        await db.execute(
            """INSERT INTO scrape_jobs (
                   id, file_path, output_dir, status, created_at,
                   error_message, history_record_id
               ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                "job-1",
                "/incoming/example.strm",
                "/library",
                "deleted",
                "2026-07-29T00:00:00",
                "旧错误",
                record.id,
            ),
        )
        await db.commit()

    await service.update_record_on_success(
        record.id,
        folder_path="/incoming/example.strm => /library/Show/Season 1/Show - S01E02.strm",
        duration_seconds=1.25,
        title="Show",
        season_number=1,
        episode_number=2,
    )

    updated = await service.get_record(record.id)
    assert updated is not None
    assert updated.status == TaskStatus.SUCCESS
    assert updated.success_count == 1
    assert updated.failed_count == 0
    assert updated.error_message is None
    assert updated.conflict_type is None
    assert updated.conflict_data is None
    assert updated.folder_path.endswith("Show - S01E02.strm")

    async with aiosqlite.connect(temp_db) as db:
        await configure_connection(db)
        cursor = await db.execute(
            "SELECT status, error_message FROM scrape_jobs WHERE id = ?",
            ("job-1",),
        )
        job = await cursor.fetchone()
    assert job == ("success", None)


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
    assert result["conflict_type"] == ConflictType.FILE_CONFLICT.value
    assert result["conflict_data"]["tmdb_id"] == 123
    assert result["conflict_data"]["season"] == 1
    assert result["conflict_data"]["episode"] == 1
    assert result["conflict_data"]["dest_path"] == conflict_path
    update = history_service.update_record.await_args
    assert update.kwargs["status"] == TaskStatus.PENDING_ACTION
    assert update.kwargs["conflict_type"] == ConflictType.FILE_CONFLICT
    assert update.kwargs["conflict_data"]["dest_path"] == conflict_path


@pytest.mark.asyncio
async def test_manual_match_success_uses_canonical_success_update(monkeypatch):
    """Manual rematches store the destination and clear stale conflict state."""
    scraper = SimpleNamespace(
        scrape_by_id=AsyncMock(
            return_value=SimpleNamespace(
                status=ScrapeStatus.SUCCESS,
                series_info=SimpleNamespace(
                    name="School",
                    original_name="School",
                    overview="",
                    poster_path="/poster.jpg",
                    first_air_date="2011-11-25",
                    vote_average=4.0,
                    genres=["动画"],
                ),
                episode_info=SimpleNamespace(
                    name="第 2 集",
                    overview="",
                    still_path="/still.jpg",
                    air_date="2012-07-13",
                ),
                parsed_season=1,
                parsed_episode=2,
                dest_path="/library/School (2011)/Season 1/School - S01E02.strm",
            )
        )
    )
    monkeypatch.setattr("server.core.container.get_scraper_service", lambda: scraper)
    record = SimpleNamespace(
        scrape_logs=[],
        manual_job_id=None,
        conflict_data={"tmdb_id": 123},
        folder_path="/incoming/school-II.strm",
    )
    history_service = AsyncMock()
    history_service.get_record.return_value = record
    history_service.clear_log_cache = Mock()
    request = ScrapeByIdRequest(
        file_path=record.folder_path,
        tmdb_id=97995,
        season=1,
        episode=2,
        output_dir="/library",
    )

    result = await history_api._execute_scrape_and_update(
        history_service, "record-1", request, "用户重新匹配"
    )

    assert result["success"] is True
    success_update = history_service.update_record_on_success.await_args
    assert success_update.kwargs["folder_path"].endswith(
        "=> /library/School (2011)/Season 1/School - S01E02.strm"
    )
    assert success_update.kwargs["title"] == "School"
    assert success_update.kwargs["season_number"] == 1
    assert success_update.kwargs["episode_number"] == 2
    history_service.update_record.assert_not_awaited()


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
