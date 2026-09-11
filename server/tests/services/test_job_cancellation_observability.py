"""Cloud-only regression coverage for cancellation and runtime metrics."""

import asyncio

import aiosqlite
import pytest

import server.services.manual_job_service as manual_jobs
import server.services.scrape_job_service as scrape_jobs
from server.core.db import create_all_tables
from server.models.manual_job import ManualJobCreate, ManualJobStatus
from server.models.scrape_job import ScrapeJobCreate, ScrapeJobSource, ScrapeJobStatus
from server.services.job_monitor_service import JobMonitorService
from server.services.manual_job_service import ManualJobService
from server.services.scrape_job_service import ScrapeJobService


async def initialize(db_path) -> None:
    async with aiosqlite.connect(db_path) as db:
        await create_all_tables(db)
        await db.commit()


class FakeNotifier:
    async def notify_job_created(self, *args, **kwargs) -> None:
        return None

    async def notify_cancelled(self, *args, **kwargs) -> None:
        return None


@pytest.mark.asyncio
async def test_pending_scrape_job_is_cancelled_and_cannot_be_claimed(
    temp_db, monkeypatch
) -> None:
    await initialize(temp_db)
    monkeypatch.setattr(scrape_jobs, "_ensure_worker", lambda: None)
    monkeypatch.setattr(scrape_jobs, "_scrape_queue", asyncio.Queue())
    monkeypatch.setattr(scrape_jobs, "get_notifier", lambda: FakeNotifier())

    service = ScrapeJobService(temp_db)
    created = await service.create_job(
        ScrapeJobCreate(file_path="/incoming/a.mkv", output_dir="/library")
    )
    assert created is not None

    cancelled, changed, _ = await service.cancel_job(created.id)

    assert changed is True
    assert cancelled is not None
    assert cancelled.status == ScrapeJobStatus.CANCELLED
    assert await service.claim_job(created.id) is False


@pytest.mark.asyncio
async def test_running_scrape_job_cancels_live_task_before_returning(
    temp_db, monkeypatch
) -> None:
    await initialize(temp_db)
    monkeypatch.setattr(scrape_jobs, "_ensure_worker", lambda: None)
    monkeypatch.setattr(scrape_jobs, "_scrape_queue", asyncio.Queue())
    monkeypatch.setattr(scrape_jobs, "get_notifier", lambda: FakeNotifier())

    service = ScrapeJobService(temp_db)
    created = await service.create_job(
        ScrapeJobCreate(file_path="/incoming/running.mkv", output_dir="/library")
    )
    assert created is not None
    assert await service.claim_job(created.id) is True

    started = asyncio.Event()

    async def live_work() -> None:
        started.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(live_work())
    scrape_jobs._active_job_tasks[created.id] = task
    try:
        await started.wait()
        updated, changed, _ = await service.cancel_job(created.id)
        assert changed is True
        assert task.cancelled()
        assert updated is not None
        assert updated.status == ScrapeJobStatus.CANCELLED
    finally:
        scrape_jobs._active_job_tasks.pop(created.id, None)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_manual_cancel_cascades_to_unfinished_scrape_jobs(
    temp_db, monkeypatch
) -> None:
    await initialize(temp_db)
    monkeypatch.setattr(manual_jobs, "_ensure_worker", lambda: None)
    monkeypatch.setattr(manual_jobs, "_job_queue", asyncio.Queue())
    monkeypatch.setattr(scrape_jobs, "_ensure_worker", lambda: None)
    monkeypatch.setattr(scrape_jobs, "_scrape_queue", asyncio.Queue())
    monkeypatch.setattr(scrape_jobs, "get_notifier", lambda: FakeNotifier())

    manual_service = ManualJobService(temp_db)
    manual = await manual_service.create_job(
        ManualJobCreate(scan_path="/incoming", target_folder="/library")
    )
    scrape_service = ScrapeJobService(temp_db)
    child = await scrape_service.create_job(
        ScrapeJobCreate(
            file_path="/incoming/a.mkv",
            output_dir="/library",
            source=ScrapeJobSource.MANUAL,
            source_id=manual.id,
        )
    )
    assert child is not None

    updated, changed, child_count, _ = await manual_service.cancel_job(manual.id)

    assert changed is True
    assert child_count == 1
    assert updated is not None
    assert updated.status == ManualJobStatus.CANCELLED
    assert (await scrape_service.get_job(child.id)).status == ScrapeJobStatus.CANCELLED


@pytest.mark.asyncio
async def test_active_jobs_must_be_cancelled_before_delete(temp_db, monkeypatch) -> None:
    await initialize(temp_db)
    monkeypatch.setattr(scrape_jobs, "_ensure_worker", lambda: None)
    monkeypatch.setattr(scrape_jobs, "_scrape_queue", asyncio.Queue())
    monkeypatch.setattr(scrape_jobs, "get_notifier", lambda: FakeNotifier())
    service = ScrapeJobService(temp_db)
    created = await service.create_job(
        ScrapeJobCreate(file_path="/incoming/a.mkv", output_dir="/library")
    )
    assert created is not None

    with pytest.raises(ValueError, match="请先取消"):
        await service.delete_jobs([created.id])


@pytest.mark.asyncio
async def test_runtime_metrics_report_persisted_and_live_state(temp_db, monkeypatch) -> None:
    await initialize(temp_db)
    monkeypatch.setattr(manual_jobs, "_ensure_worker", lambda: None)
    monkeypatch.setattr(manual_jobs, "_job_queue", asyncio.Queue())
    monkeypatch.setattr(scrape_jobs, "_ensure_worker", lambda: None)
    monkeypatch.setattr(scrape_jobs, "_scrape_queue", asyncio.Queue())
    monkeypatch.setattr(scrape_jobs, "get_notifier", lambda: FakeNotifier())

    await ManualJobService(temp_db).create_job(
        ManualJobCreate(scan_path="/incoming", target_folder="/library")
    )
    await ScrapeJobService(temp_db).create_job(
        ScrapeJobCreate(file_path="/incoming/a.mkv", output_dir="/library")
    )

    metrics = await JobMonitorService(temp_db).get_metrics()

    assert metrics.manual.status_counts["pending"] == 1
    assert metrics.manual.queued_in_memory == 1
    assert metrics.scrape.status_counts["pending"] == 1
    assert metrics.scrape.queued_in_memory == 1
    assert metrics.file_io.workers == 2
