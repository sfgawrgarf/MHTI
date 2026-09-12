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
async def test_delete_record_keeps_linked_scrape_job_readable(temp_db):
    """The deleted audit state must be valid in both history and job models."""
    async with aiosqlite.connect(temp_db) as db:
        await configure_connection(db)
        await create_all_tables(db)
        await db.execute(
            """INSERT INTO history_records
               (id, task_name, folder_path, executed_at, status, total_files,
                success_count, failed_count, duration_seconds, scrape_job_id)
               VALUES ('history-pending', 'pending', '/incoming/pending.mkv',
                       CURRENT_TIMESTAMP, 'pending_action', 1, 0, 0, 0, 'job-pending')"""
        )
        await db.execute(
            """INSERT INTO scrape_jobs
               (id, file_path, output_dir, source, status, created_at, history_record_id)
               VALUES ('job-pending', '/incoming/pending.mkv', '/library', 'manual',
                       'pending_action', CURRENT_TIMESTAMP, 'history-pending')"""
        )
        await db.commit()

    service = HistoryService(db_path=temp_db)
    assert await service.delete_record("history-pending") is True

    from server.models.scrape_job import ScrapeJobStatus
    from server.services.scrape_job_service import ScrapeJobService

    job = await ScrapeJobService(db_path=temp_db).get_job("job-pending")
    assert job is not None
    assert job.status == ScrapeJobStatus.DELETED


@pytest.mark.asyncio
async def test_delete_record_rejects_running_job(temp_db):
    """Deleting history must never detach a live filesystem operation."""
    async with aiosqlite.connect(temp_db) as db:
        await configure_connection(db)
        await create_all_tables(db)
        await db.execute(
            """INSERT INTO history_records
               (id, task_name, folder_path, executed_at, status, total_files,
                success_count, failed_count, duration_seconds, scrape_job_id)
               VALUES ('history-running', 'running', '/incoming/live.mkv',
                       CURRENT_TIMESTAMP, 'running', 1, 0, 0, 0, 'job-running')"""
        )
        await db.execute(
            """INSERT INTO scrape_jobs
               (id, file_path, output_dir, source, status, created_at, history_record_id)
               VALUES ('job-running', '/incoming/live.mkv', '/library', 'manual',
                       'running', CURRENT_TIMESTAMP, 'history-running')"""
        )
        await db.commit()

    service = HistoryService(db_path=temp_db)
    with pytest.raises(ValueError, match="请先取消任务"):
        await service.delete_record("history-running")

    record = await service.get_record("history-running")
    assert record is not None
    assert record.status == TaskStatus.RUNNING
    async with aiosqlite.connect(temp_db) as db:
        cursor = await db.execute("SELECT status FROM scrape_jobs WHERE id = 'job-running'")
        assert (await cursor.fetchone())[0] == "running"


@pytest.mark.asyncio
async def test_clear_records_is_atomic_when_a_job_is_running(temp_db):
    """A clear-all request rejects the whole operation instead of partially deleting."""
    async with aiosqlite.connect(temp_db) as db:
        await configure_connection(db)
        await create_all_tables(db)
        for record_id, status, job_id in (
            ("history-done", "success", "job-done"),
            ("history-running", "running", "job-running"),
        ):
            await db.execute(
                """INSERT INTO history_records
                   (id, task_name, folder_path, executed_at, status, total_files,
                    success_count, failed_count, duration_seconds, scrape_job_id)
                   VALUES (?, ?, ?, CURRENT_TIMESTAMP, ?, 1, 0, 0, 0, ?)""",
                (record_id, record_id, f"/incoming/{record_id}.mkv", status, job_id),
            )
            await db.execute(
                """INSERT INTO scrape_jobs
                   (id, file_path, output_dir, source, status, created_at, history_record_id)
                   VALUES (?, ?, '/library', 'manual', ?, CURRENT_TIMESTAMP, ?)""",
                (job_id, f"/incoming/{record_id}.mkv", status, record_id),
            )
        await db.commit()

    service = HistoryService(db_path=temp_db)
    with pytest.raises(ValueError, match="有 1 条记录"):
        await service.clear_records()

    records, total = await service.list_records(limit=10)
    assert total == 2
    assert {record.id for record in records} == {"history-done", "history-running"}

    async with aiosqlite.connect(temp_db) as db:
        await db.execute(
            "UPDATE history_records SET status = 'cancelled' WHERE id = 'history-running'"
        )
        await db.execute(
            "UPDATE scrape_jobs SET status = 'cancelled' WHERE id = 'job-running'"
        )
        await db.commit()

    assert await service.clear_records() == 2
    records, total = await service.list_records(limit=10)
    assert records == []
    assert total == 0
    async with aiosqlite.connect(temp_db) as db:
        cursor = await db.execute("SELECT COUNT(*) FROM scrape_jobs")
        assert (await cursor.fetchone())[0] == 0


@pytest.mark.asyncio
async def test_history_delete_endpoints_report_active_conflict():
    history_service = AsyncMock()
    history_service.delete_record.side_effect = ValueError("请先取消任务")
    history_service.clear_records.side_effect = ValueError("请先取消任务")

    with pytest.raises(HTTPException) as delete_error:
        await history_api.delete_record("history-running", history_service)
    assert delete_error.value.status_code == 409
    assert delete_error.value.detail == "请先取消任务"

    with pytest.raises(HTTPException) as clear_error:
        await history_api.clear_records(None, history_service)
    assert clear_error.value.status_code == 409
    assert clear_error.value.detail == "请先取消任务"


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
async def test_manual_match_is_queued_without_synchronous_execution(monkeypatch):
    """Manual actions must use the persistent worker queue, not the HTTP handler."""
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
        scrape_job_id="original-job",
        conflict_data={"parsed_title": "example"},
    )
    history_service = AsyncMock()
    history_service.get_record.return_value = record
    history_service.clear_log_cache = Mock()
    queue = SimpleNamespace(create_job=AsyncMock(return_value=SimpleNamespace(id="queued-job")))
    monkeypatch.setattr("server.services.scrape_job_service.ScrapeJobService", lambda **kwargs: queue)
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

    assert result["queued"] is True
    assert result["job_id"] == "queued-job"
    scraper.scrape_by_id.assert_not_awaited()
    queued = queue.create_job.call_args.args[0]
    assert queued.continuation_history_id == "record-1"
    assert queued.replaces_job_id == "original-job"
    assert queued.correction_tmdb_id == 123
    assert queued.correction_season == 1
    assert queued.correction_episode == 1
    history_service.update_record.assert_not_awaited()


@pytest.mark.asyncio
async def test_manual_match_preserves_selection_in_queued_job(monkeypatch):
    """The worker receives the new selection, output and user log intact."""
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
        scrape_job_id="original-job",
        conflict_data={"tmdb_id": 123},
        folder_path="/incoming/school-II.strm",
    )
    history_service = AsyncMock()
    history_service.get_record.return_value = record
    history_service.clear_log_cache = Mock()
    queue = SimpleNamespace(create_job=AsyncMock(return_value=SimpleNamespace(id="queued-job")))
    monkeypatch.setattr("server.services.scrape_job_service.ScrapeJobService", lambda **kwargs: queue)
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
    assert result["queued"] is True
    queued = queue.create_job.call_args.args[0]
    assert queued.output_dir == "/library"
    assert queued.correction_tmdb_id == 97995
    assert queued.correction_episode == 2
    assert queued.selection_log == "用户重新匹配"
    scraper.scrape_by_id.assert_not_awaited()
    history_service.update_record_on_success.assert_not_awaited()
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
