"""Batch outcomes across dispatch, recovery, retry, correction and cancellation."""

import asyncio

import aiosqlite
import pytest

from server.application import manual_job_service as manual_module
from server.application.manual_job_service import ManualJobService
from server.models.history import HistoryRecordCreate, TaskStatus
from server.models.manual_job import LinkMode, ManualJobAdvancedSettings, ManualJobCreate, ManualJobStatus
from server.models.organize import OrganizeMode
from server.models.scrape_job import ScrapeJobCreate, ScrapeJobStatus
from server.tests.application.test_audit_remediation import audit_env  # noqa: F401


async def batch(env, monkeypatch, *, dispatched=1, total=1):
    monkeypatch.setattr(manual_module, '_ensure_worker', lambda: None)
    monkeypatch.setattr(manual_module, '_job_queue', asyncio.Queue())
    service = ManualJobService(db_path=env.db)
    job = await service.create_job(ManualJobCreate(
        scan_path=str(env.source), target_folder=str(env.output), link_mode=LinkMode.COPY,
        advanced_settings=ManualJobAdvancedSettings(use_global_organize=False, file_size_filter=0),
    ))
    await service.update_job_status(job.id, ManualJobStatus.RUNNING, total_count=total,
                                    dispatched_count=dispatched)
    return service, job


async def child(env, parent, *, operation='scrape', **kwargs):
    return await env.jobs.create_job(ScrapeJobCreate(
        file_path=str(env.source), output_dir=str(env.output), link_mode=OrganizeMode.COPY,
        source_id=parent.id, operation=operation, **kwargs,
    ), skip_duplicate_check=True)


async def outcome(env, parent, job, status):
    return await env.history.create_record(HistoryRecordCreate(
        task_name='batch outcome', folder_path=str(env.source), manual_job_id=parent.id,
        scrape_job_id=job.id, status=status, total_files=1,
        success_count=int(status == TaskStatus.SUCCESS), failed_count=0, duration_seconds=0,
    ))


@pytest.mark.asyncio
async def test_dispatch_waits_for_real_child_result(audit_env, monkeypatch):
    env = audit_env
    service, parent = await batch(env, monkeypatch, dispatched=None)
    # Run the real scan + durable enqueue path, without consuming child jobs.
    await service.update_job_status(parent.id, ManualJobStatus.PENDING)
    await manual_module._execute_job(service, parent.id)
    current = await service.get_job(parent.id)
    assert current.status == ManualJobStatus.RUNNING
    assert current.success_count == 0 and current.child_pending_count == 1
    jobs, _ = await env.jobs.list_jobs(source_id=parent.id)
    await env.jobs.update_job(jobs[0].id, status=ScrapeJobStatus.SUCCESS)
    current = await service.get_job(parent.id)
    assert current.status == ManualJobStatus.SUCCESS and current.success_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('terminal', [ScrapeJobStatus.FAILED, ScrapeJobStatus.TIMEOUT])
async def test_failure_before_history_creation_finishes_parent(audit_env, monkeypatch, terminal):
    service, parent = await batch(audit_env, monkeypatch)
    job = await child(audit_env, parent)
    await audit_env.jobs.update_job(job.id, status=terminal)
    current = await service.get_job(parent.id)
    assert current.status == ManualJobStatus.FAILED
    assert current.error_count == 1 and current.success_count == 0


@pytest.mark.asyncio
async def test_pending_action_is_not_a_finished_batch(audit_env, monkeypatch):
    service, parent = await batch(audit_env, monkeypatch)
    job = await child(audit_env, parent)
    await outcome(audit_env, parent, job, TaskStatus.PENDING_ACTION)
    await audit_env.jobs.update_job(job.id, status=ScrapeJobStatus.PENDING_ACTION)
    current = await service.get_job(parent.id)
    assert current.status == ManualJobStatus.RUNNING and current.skip_count == 0


@pytest.mark.asyncio
async def test_duplicate_dispatch_has_searchable_skip_record(audit_env, monkeypatch):
    env = audit_env
    # Another source owns the pending file; the new batch must explain its skip.
    await env.jobs.create_job(ScrapeJobCreate(file_path=str(env.source), output_dir=str(env.output)))
    service, parent = await batch(env, monkeypatch, dispatched=None)
    await service.update_job_status(parent.id, ManualJobStatus.PENDING)
    await manual_module._execute_job(service, parent.id)
    current = await service.get_job(parent.id)
    assert current.status == ManualJobStatus.SUCCESS
    assert (current.success_count, current.skip_count) == (0, 1)
    records, total = await env.history.list_records(manual_job_id=parent.id)
    assert total == 1 and records[0].status == TaskStatus.SKIPPED
    assert records[0].folder_path == str(env.source)
    assert '已有处理中' in records[0].error_message
    for _ in range(3):
        await service.refresh_progress(parent.id)
    assert (await env.history.list_records(manual_job_id=parent.id))[1] == 1
    assert (await service.get_job(parent.id)).skip_count == 1


@pytest.mark.asyncio
async def test_dispatch_checkpoint_is_not_rescanned_after_restart(audit_env, monkeypatch):
    service, parent = await batch(audit_env, monkeypatch)
    job = await child(audit_env, parent)
    assert parent.id not in await service.prepare_recovery()
    assert (await service.get_job(parent.id)).status == ManualJobStatus.RUNNING
    await audit_env.jobs.update_job(job.id, status=ScrapeJobStatus.SUCCESS)
    assert (await service.get_job(parent.id)).success_count == 1


@pytest.mark.asyncio
async def test_partial_dispatch_recognizes_its_own_child_on_resume(audit_env, monkeypatch):
    service, parent = await batch(audit_env, monkeypatch, dispatched=None)
    original = await child(audit_env, parent)
    assert parent.id in await service.prepare_recovery()
    await manual_module._execute_job(service, parent.id)
    jobs, total = await audit_env.jobs.list_jobs(source_id=parent.id)
    assert total == 1 and jobs[0].id == original.id
    assert (await audit_env.history.list_records(manual_job_id=parent.id))[1] == 0
    current = await service.get_job(parent.id)
    assert current.status == ManualJobStatus.RUNNING and current.skip_count == 0


@pytest.mark.asyncio
async def test_retry_reuses_history_without_inflating_progress(audit_env, monkeypatch):
    env = audit_env
    service, parent = await batch(env, monkeypatch)
    old = await child(env, parent)
    record = await outcome(env, parent, old, TaskStatus.FAILED)
    await env.jobs.update_job(old.id, status=ScrapeJobStatus.REPLACED)
    new = await child(env, parent, operation='retry', replaces_job_id=old.id,
                      continuation_history_id=record.id)
    await env.history.update_record(record.id, status=TaskStatus.SUCCESS)
    await env.jobs.update_job(new.id, status=ScrapeJobStatus.SUCCESS)
    await asyncio.gather(*(service.refresh_progress(parent.id) for _ in range(5)))
    current = await service.get_job(parent.id)
    assert current.status == ManualJobStatus.SUCCESS
    assert (current.success_count, current.error_count, current.skip_count) == (1, 0, 0)


@pytest.mark.asyncio
async def test_failed_correction_keeps_one_successful_original(audit_env, monkeypatch):
    env = audit_env
    service, parent = await batch(env, monkeypatch)
    old = await child(env, parent)
    record = await outcome(env, parent, old, TaskStatus.SUCCESS)
    await env.jobs.update_job(old.id, status=ScrapeJobStatus.SUCCESS)
    correction = await child(env, parent, operation='rematch', replaces_job_id=old.id,
                             correction_history_id=record.id)
    await outcome(env, parent, correction, TaskStatus.FAILED)
    await env.jobs.update_job(correction.id, status=ScrapeJobStatus.FAILED)
    current = await service.get_job(parent.id)
    assert current.status == ManualJobStatus.SUCCESS
    assert current.success_count == 1 and current.error_count == 0


@pytest.mark.asyncio
async def test_cancelled_parent_is_never_reopened_by_refresh(audit_env, monkeypatch):
    service, parent = await batch(audit_env, monkeypatch)
    job = await child(audit_env, parent)
    await service.update_job_status(parent.id, ManualJobStatus.CANCELLED)
    await audit_env.jobs.update_job(job.id, status=ScrapeJobStatus.SUCCESS)
    await asyncio.gather(*(service.refresh_progress(parent.id) for _ in range(3)))
    assert (await service.get_job(parent.id)).status == ManualJobStatus.CANCELLED


@pytest.mark.asyncio
async def test_legacy_dispatch_success_is_repaired_from_children(audit_env, monkeypatch):
    service, parent = await batch(audit_env, monkeypatch, dispatched=None)
    job = await child(audit_env, parent)
    await service.update_job_status(parent.id, ManualJobStatus.SUCCESS, success_count=9)
    current = await service.get_job(parent.id)
    assert current.status == ManualJobStatus.RUNNING and current.success_count == 0
    await audit_env.jobs.update_job(job.id, status=ScrapeJobStatus.SUCCESS)
    current = await service.get_job(parent.id)
    assert current.status == ManualJobStatus.SUCCESS and current.success_count == 1


@pytest.mark.asyncio
async def test_legacy_schema_migration_preserves_jobs(audit_env, monkeypatch):
    service, parent = await batch(audit_env, monkeypatch, dispatched=None)
    async with aiosqlite.connect(audit_env.db) as db:
        await db.execute('ALTER TABLE manual_jobs DROP COLUMN dispatched_count')
        await db.commit()
    restarted = ManualJobService(db_path=audit_env.db)
    assert (await restarted.get_job(parent.id)).dispatched_count is None
