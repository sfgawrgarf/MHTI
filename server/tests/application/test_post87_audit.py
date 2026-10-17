"""Cloud audit probes for state ownership after output commitment (main f5ffb34)."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import aiosqlite
import pytest

from server.tests.application.test_recovery_regressions import recovery_env, bind_worker  # noqa: F401
from server.tests.application.test_audit_remediation import audit_env  # noqa: F401
from server.tests.application.test_file_operation_recovery import running_job
from server.tests.application.test_commit_cleanup import committed_correction
from server.tests.application.test_scraper_service import FakeTMDB, _build_service, _patch_tail
from server.application import scrape_job_service as jobs_module
from server.application.file_recovery import reconcile_file_operations
from server.application.history_actions import HistoryScrapeActions
from server.infrastructure import file_journal as journal
from server.infrastructure.file_operations import publish_file
from server.models.history import TaskStatus
from server.models.organize import OrganizeMode
from server.models.scrape_job import ScrapeJobCreate, ScrapeJobStatus


@pytest.mark.asyncio
async def test_old_cleanup_receipt_cannot_change_newer_history_owner(recovery_env, monkeypatch):
    env = recovery_env
    old, record = await running_job(env, link_mode=OrganizeMode.COPY)
    target = env.output / 'Show' / 'Season 1' / 'old-name.mkv'
    token = journal.owner.set(old.id)
    try:
        publish_file(env.source, target, OrganizeMode.COPY)
    finally:
        journal.owner.reset(token)
    await env.jobs.update_job(old.id, status=ScrapeJobStatus.SUCCESS)
    await env.history.update_record(record.id, status=TaskStatus.SUCCESS,
                                    folder_path=f'{env.source} => {target}')
    await env.scraped.register_output(history_record_id=record.id,
                                     source_path=str(env.source), target_path=str(target),
                                     tmdb_id=123, title='Show', season=1, episode=10)
    # A restart after business commit but before cleanup leaves this receipt.
    assert journal.read_records(old.id)
    scraper = _build_service(env.db, FakeTMDB())
    _patch_tail(monkeypatch, scraper)
    bind_worker(env, monkeypatch, scraper)
    actions = HistoryScrapeActions(env.history, env.jobs, env.scraped, env.files)
    queued = await actions.queue_record_operation(record.id, operation='reorganize')
    await jobs_module._execute_scrape_job(env.jobs, queued['job_id'])
    current = await env.history.get_record(record.id)
    assert current.scrape_job_id == queued['job_id'] and current.status == TaskStatus.SUCCESS
    assert (await env.jobs.get_job(old.id)).status == ScrapeJobStatus.REPLACED
    registered = await env.scraped.list_by_history_record(record.id)
    assert Path(registered[0].target_path).read_bytes() == b'original video'
    assert not target.exists()
    await reconcile_file_operations(env.jobs, old.id)
    assert (await env.history.get_record(record.id)).status == TaskStatus.SUCCESS
    assert (await env.jobs.get_job(old.id)).status == ScrapeJobStatus.REPLACED


@pytest.mark.asyncio
async def test_shutdown_after_business_commit_preserves_successful_history(recovery_env, monkeypatch):
    env = recovery_env
    scraper = _build_service(env.db, FakeTMDB())
    _patch_tail(monkeypatch, scraper)
    bind_worker(env, monkeypatch, scraper)
    completed = asyncio.Event()
    notifier = AsyncMock()

    async def pause_completed_notification(*args, **kwargs):
        completed.set()
        await asyncio.Event().wait()

    notifier.notify_completed.side_effect = pause_completed_notification
    monkeypatch.setattr(jobs_module, 'get_notifier', lambda: notifier)
    job = await env.jobs.create_job(ScrapeJobCreate(
        file_path=str(env.source), output_dir=str(env.output), metadata_dir=str(env.metadata),
        link_mode=OrganizeMode.COPY, correction_tmdb_id=123, correction_season=1, correction_episode=1,
    ))
    task = asyncio.create_task(jobs_module._execute_scrape_job(env.jobs, job.id))
    try:
        await asyncio.wait_for(completed.wait(), 5)
        before = await env.jobs.get_job(job.id)
        assert before.status == ScrapeJobStatus.SUCCESS and before.output_committed_at
        monkeypatch.setattr(jobs_module, '_workers_stopping', True)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    after = await env.jobs.get_job(job.id)
    assert after.status == ScrapeJobStatus.SUCCESS
    assert (await env.history.get_record(after.history_record_id)).status == TaskStatus.SUCCESS


@pytest.mark.asyncio
async def test_cleanup_result_db_error_does_not_leave_unrecoverable_pending_state(recovery_env, monkeypatch):
    env = recovery_env
    _, _, job, record, target, plan = await committed_correction(env)

    async def unavailable(*args, **kwargs):
        raise aiosqlite.OperationalError('transient database busy while recording cleanup result')

    with monkeypatch.context() as patch:
        patch.setattr(env.jobs._repo, 'record_cleanup_result', unavailable)
        await reconcile_file_operations(env.jobs, job.id)
    # Cleanup has already removed the only startup recovery work list.
    assert journal.read_records(job.id) == []
    assert not plan.directory.exists()
    assert target.with_suffix('.nfo').read_text() == 'correct metadata'
    await env.jobs.prepare_recovery()
    assert (await env.jobs.get_job(job.id)).status == ScrapeJobStatus.SUCCESS
    assert (await env.history.get_record(record.id)).status == TaskStatus.SUCCESS
