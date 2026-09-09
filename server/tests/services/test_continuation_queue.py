"""Persistent manual actions share the normal scrape queue and survive restart."""

import asyncio
from unittest.mock import AsyncMock

import aiosqlite
import pytest

from server.api.history import _execute_scrape_and_update
from server.core.db import create_all_tables
from server.models.history import ConflictType, HistoryRecordCreate, TaskStatus
from server.models.organize import OrganizeMode
from server.models.scrape_job import ScrapeJobCreate, ScrapeJobStatus
from server.models.scraper import ScrapeByIdRequest
from server.services.history_service import HistoryService
from server.services.scrape_job_service import ScrapeJobService


async def setup_queue(temp_db, tmp_path, monkeypatch):
    import server.services.scrape_job_service as jobs

    async with aiosqlite.connect(temp_db) as db:
        await create_all_tables(db)
        await db.commit()
    monkeypatch.setattr(jobs, "_ensure_worker", lambda: None)
    monkeypatch.setattr(jobs, "_scrape_queue", asyncio.Queue())
    monkeypatch.setattr(jobs, "get_notifier", lambda: AsyncMock())
    service = ScrapeJobService(db_path=temp_db)
    old = await service.create_job(ScrapeJobCreate(
        file_path=str(tmp_path / "show.strm"), output_dir=str(tmp_path / "out"),
        metadata_dir=str(tmp_path / "meta"), link_mode=OrganizeMode.COPY,
    ))
    await service.update_job(old.id, status=ScrapeJobStatus.PENDING_ACTION)
    history = HistoryService(db_path=temp_db)
    record = await history.create_record(HistoryRecordCreate(
        task_name="Example", folder_path=old.file_path, status=TaskStatus.PENDING_ACTION,
        total_files=1, success_count=0, failed_count=0, duration_seconds=0,
        scrape_job_id=old.id, conflict_type=ConflictType.FILE_CONFLICT,
    ))
    request = ScrapeByIdRequest(
        file_path=old.file_path, output_dir=old.output_dir, metadata_dir=old.metadata_dir,
        link_mode=old.link_mode, tmdb_id=123, season=0, episode=1, file_action="overwrite", skip_emby_check=True,
    )
    return service, history, record, request, old


@pytest.mark.asyncio
async def test_queue_persists_context_and_reuses_history(temp_db, tmp_path, monkeypatch):
    service, history, record, request, old = await setup_queue(temp_db, tmp_path, monkeypatch)
    result = await _execute_scrape_and_update(history, record.id, request, "用户选择特别篇")
    assert result["queued"] is True
    job = await service.get_job(result["job_id"])
    assert job.status == ScrapeJobStatus.PENDING
    assert job.correction_season == 0
    assert job.file_action == "overwrite"
    assert job.skip_emby_check is True
    assert job.selection_log == "用户选择特别篇"
    assert job.continuation_history_id == record.id
    assert job.output_dir == request.output_dir
    assert job.metadata_dir == request.metadata_dir
    assert job.link_mode == OrganizeMode.COPY
    assert (await service.get_job(old.id)).status == ScrapeJobStatus.REPLACED
    updated = await history.get_record(record.id)
    assert updated.status == TaskStatus.RUNNING
    assert updated.scrape_job_id == job.id
    _, total = await history.list_records()
    assert total == 1


@pytest.mark.asyncio
async def test_concurrent_submissions_enqueue_only_once(temp_db, tmp_path, monkeypatch):
    from fastapi import HTTPException

    service, history, record, request, _ = await setup_queue(temp_db, tmp_path, monkeypatch)
    results = await asyncio.gather(
        _execute_scrape_and_update(history, record.id, request),
        _execute_scrape_and_update(history, record.id, request),
        return_exceptions=True,
    )
    assert sum(isinstance(item, dict) and item["queued"] for item in results) == 1
    rejected = next(item for item in results if isinstance(item, Exception))
    assert isinstance(rejected, HTTPException)
    assert rejected.status_code == 409
    job = await service.get_job((await history.get_record(record.id)).scrape_job_id)
    assert job.continuation_history_id == record.id


@pytest.mark.asyncio
@pytest.mark.parametrize("running", [False, True])
async def test_restart_recovers_manual_choice_without_creating_history(temp_db, tmp_path, monkeypatch, running):
    service, history, record, request, _ = await setup_queue(temp_db, tmp_path, monkeypatch)
    result = await _execute_scrape_and_update(history, record.id, request, "重试")
    job_id = result["job_id"]
    if running:
        assert await service.claim_job(job_id)
        await service.update_job(job_id, history_record_id=record.id)
    recovered = await service.prepare_recovery()
    assert job_id in recovered
    job = await service.get_job(job_id)
    assert job.status == ScrapeJobStatus.PENDING
    assert job.continuation_history_id == record.id
    assert job.correction_season == 0
    assert job.file_action == "overwrite"
    assert job.selection_log == "重试"
    _, total = await history.list_records()
    assert total == 1


@pytest.mark.asyncio
async def test_active_same_file_blocks_manual_continuation(temp_db, tmp_path, monkeypatch):
    from fastapi import HTTPException

    service, history, record, request, old = await setup_queue(temp_db, tmp_path, monkeypatch)
    await service.update_job(old.id, status=ScrapeJobStatus.RUNNING)
    with pytest.raises(HTTPException) as caught:
        await _execute_scrape_and_update(history, record.id, request)
    assert caught.value.status_code == 409
    assert (await history.get_record(record.id)).status == TaskStatus.PENDING_ACTION
