"""Committed correction outcomes survive cleanup faults and concurrent readers."""

import asyncio
from pathlib import Path
import threading

import pytest

from server.tests.application.test_recovery_regressions import recovery_env  # noqa: F401
from server.tests.application.test_audit_remediation import audit_env  # noqa: F401
from server.tests.application.test_file_operation_recovery import running_job
from server.application.file_recovery import reconcile_file_operations
from server.infrastructure import file_journal as journal
from server.infrastructure.file_operations import publish_file
from server.models.history import TaskStatus
from server.models.organize import OrganizeMode
from server.models.scrape_job import ScrapeJobStatus


async def committed_correction(env, *, complete=True):
    old, old_record = await running_job(env)
    await env.jobs.update_job(old.id, status=ScrapeJobStatus.SUCCESS)
    await env.history.update_record(old_record.id, status=TaskStatus.SUCCESS)
    job, record = await running_job(env, operation='rematch',
                                     correction_history_id=old_record.id, replaces_job_id=old.id)
    target = env.output / 'Show' / 'Season 1' / '01.mkv'
    target.parent.mkdir(parents=True)
    nfo = target.with_suffix('.nfo')
    nfo.write_text('old metadata')
    token = journal.owner.set(job.id)
    try:
        plan = journal.MetadataPlan.create(target, None)
        (plan.content / 'Show' / 'Season 1' / '01.nfo').write_text('correct metadata')
        plan.prepare()
        publish_file(env.source, target, OrganizeMode.COPY)
        plan.commit()
    finally:
        journal.owner.reset(token)
    if complete:
        await env.history.update_record(record.id, status=TaskStatus.SUCCESS)
        await env.jobs._repo.complete_correction(job.id)
    return old, old_record, job, record, target, plan


@pytest.mark.asyncio
async def test_cleanup_failure_never_reverts_committed_correction(recovery_env, monkeypatch):
    env = recovery_env
    old, old_record, job, record, target, plan = await committed_correction(env)
    cleanup = journal.MetadataPlan.cleanup
    monkeypatch.setattr(journal.MetadataPlan, 'cleanup', lambda _: (_ for _ in ()).throw(PermissionError('cleanup denied')))
    await reconcile_file_operations(env.jobs, job.id)
    after = await env.jobs.get_job(job.id)
    # A second process startup sees the same cleanup failure.
    await env.jobs.prepare_recovery()
    assert target.with_suffix('.nfo').read_text() == 'correct metadata'
    assert after.status == ScrapeJobStatus.SUCCESS
    assert (await env.history.get_record(record.id)).status == TaskStatus.SUCCESS
    assert (await env.history.get_record(old_record.id)).status == TaskStatus.REPLACED
    assert (await env.jobs.get_job(old.id)).status == ScrapeJobStatus.REPLACED
    assert after.output_committed_at is not None
    assert after.cleanup_completed_at is None
    assert after.cleanup_error
    assert journal.read_records(job.id)
    monkeypatch.setattr(journal.MetadataPlan, 'cleanup', cleanup)
    await env.jobs.prepare_recovery()
    completed = await env.jobs.get_job(job.id)
    assert completed.status == ScrapeJobStatus.SUCCESS
    assert completed.output_committed_at == after.output_committed_at
    assert completed.cleanup_completed_at is not None and completed.cleanup_error is None
    assert journal.read_records(job.id) == []
    assert not plan.directory.exists()
    assert target.with_suffix('.nfo').read_text() == 'correct metadata'


@pytest.mark.asyncio
async def test_old_pending_cleanup_state_uses_durable_commit_not_display_status(recovery_env):
    env = recovery_env
    _, old_record, job, record, target, _ = await committed_correction(env)
    await env.jobs.update_job(job.id, status=ScrapeJobStatus.PENDING_ACTION,
                              error_message='文件操作恢复待处理，清理失败')
    await env.history.update_record(record.id, status=TaskStatus.PENDING_ACTION)
    await env.jobs.prepare_recovery()
    assert target.with_suffix('.nfo').read_text() == 'correct metadata'
    assert (await env.jobs.get_job(job.id)).status == ScrapeJobStatus.SUCCESS
    assert (await env.history.get_record(record.id)).status == TaskStatus.SUCCESS
    assert (await env.history.get_record(old_record.id)).status == TaskStatus.REPLACED
    assert journal.read_records(job.id) == []


@pytest.mark.asyncio
async def test_other_worker_removing_receipt_does_not_lose_cancelled_move_path(recovery_env, monkeypatch):
    env = recovery_env
    job, record = await running_job(env)
    token = journal.owner.set(job.id)
    target = env.output / 'moved.mkv'
    try:
        publish_file(env.source, target, OrganizeMode.MOVE)
    finally:
        journal.owner.reset(token)
    await env.jobs.update_job(job.id, status=ScrapeJobStatus.CANCELLED)
    await env.history.update_record(record.id, status=TaskStatus.CANCELLED)
    other = journal.new_record('media', source='unused', destination='unused')
    other_path = journal.root() / f"{other['id']}.json"
    ready, release = threading.Event(), threading.Event()
    read_text = Path.read_text

    def paused_read(path, *args, **kwargs):
        if path == other_path:
            ready.set()
            assert release.wait(5)
        return read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'read_text', paused_read)
    recovery = asyncio.create_task(reconcile_file_operations(env.jobs, job.id))
    try:
        assert await asyncio.to_thread(ready.wait, 5)
        # The other worker finishes after enumeration but before this read.
        await asyncio.to_thread(journal.forget, other)
        release.set()
        await recovery
    finally:
        release.set()
        await asyncio.gather(recovery, return_exceptions=True)
    updated = await env.history.get_record(record.id)
    assert str(target) in updated.folder_path
    assert updated.status == TaskStatus.CANCELLED
    assert target.read_bytes() == b'original video'
    assert journal.read_records(job.id) == []


@pytest.mark.asyncio
async def test_legacy_database_recovers_committed_correction_after_cleanup_error(recovery_env):
    import aiosqlite
    from server.application.scrape_job_service import ScrapeJobService
    env = recovery_env
    _, old_record, job, record, target, _ = await committed_correction(env)
    async with aiosqlite.connect(env.db) as db:
        await db.execute("UPDATE scrape_jobs SET status = 'pending_action' WHERE id = ?", (job.id,))
        await db.execute("UPDATE history_records SET status = 'pending_action' WHERE id = ?", (record.id,))
        for column in ['output_committed_at', 'cleanup_completed_at', 'cleanup_error']:
            await db.execute(f'ALTER TABLE scrape_jobs DROP COLUMN {column}')
        await db.commit()
    restarted = ScrapeJobService(db_path=env.db)
    await restarted.prepare_recovery()
    recovered = await restarted.get_job(job.id)
    assert recovered.output_committed_at is not None
    assert recovered.cleanup_completed_at is not None
    assert recovered.status == ScrapeJobStatus.SUCCESS
    assert target.with_suffix('.nfo').read_text() == 'correct metadata'
    assert (await env.history.get_record(old_record.id)).status == TaskStatus.REPLACED


@pytest.mark.parametrize('failure', ['invalid_json', 'permission'])
def test_receipt_read_still_reports_corruption_and_permission_errors(tmp_path, monkeypatch, failure):
    import json
    record = journal.new_record('media', source='unused', destination='unused')
    path = journal.root() / f"{record['id']}.json"
    if failure == 'invalid_json':
        path.write_text('{broken')
        with pytest.raises(json.JSONDecodeError):
            journal.read_records()
    else:
        original = Path.read_text
        def denied(selected, *args, **kwargs):
            if selected == path:
                raise PermissionError('not readable')
            return original(selected, *args, **kwargs)
        monkeypatch.setattr(Path, 'read_text', denied)
        with pytest.raises(PermissionError):
            journal.read_records()
    assert path.exists()


@pytest.mark.asyncio
async def test_uncommitted_correction_still_rolls_back_with_independent_cleanup(recovery_env, monkeypatch):
    env = recovery_env
    # Publish metadata without completing the correction transaction.
    _, old_record, job, record, target, _ = await committed_correction(env, complete=False)
    await env.jobs.update_job(job.id, status=ScrapeJobStatus.CANCELLED)
    await env.history.update_record(record.id, status=TaskStatus.CANCELLED)
    cleanup = journal.MetadataPlan.cleanup
    monkeypatch.setattr(journal.MetadataPlan, 'cleanup', lambda _: (_ for _ in ()).throw(PermissionError('cleanup denied')))
    await reconcile_file_operations(env.jobs, job.id)
    assert target.with_suffix('.nfo').read_text() == 'old metadata'
    after = await env.jobs.get_job(job.id)
    assert after.status == ScrapeJobStatus.CANCELLED and after.output_committed_at is None
    assert after.cleanup_error
    assert (await env.history.get_record(old_record.id)).status == TaskStatus.SUCCESS
    monkeypatch.setattr(journal.MetadataPlan, 'cleanup', cleanup)
    await env.jobs.prepare_recovery()
    assert target.with_suffix('.nfo').read_text() == 'old metadata'
    assert (await env.jobs.get_job(job.id)).status == ScrapeJobStatus.CANCELLED
    assert journal.read_records(job.id) == []
