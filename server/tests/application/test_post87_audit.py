"""Regressions for recovery ownership, shutdown and cleanup accounting."""

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
    assert (await env.history.get_record(record.id)) == current
    assert (await env.scraped.list_by_history_record(record.id)) == registered
    assert (await env.jobs.get_job(old.id)).status == ScrapeJobStatus.REPLACED
    assert journal.read_records(old.id) == []


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
@pytest.mark.parametrize('cleanup_denied', [False, True])
async def test_cleanup_result_db_error_does_not_leave_unrecoverable_pending_state(recovery_env, monkeypatch, cleanup_denied):
    env = recovery_env
    _, _, job, record, target, plan = await committed_correction(env)

    async def unavailable(*args, **kwargs):
        raise aiosqlite.OperationalError('transient database busy while recording cleanup result')

    with monkeypatch.context() as patch:
        patch.setattr(env.jobs._repo, 'record_cleanup_result', unavailable)
        if cleanup_denied:
            def denied(_):
                raise PermissionError('cleanup temporarily denied')
            patch.setattr(journal.MetadataPlan, 'cleanup', denied)
        await reconcile_file_operations(env.jobs, job.id)
    assert bool(journal.read_records(job.id)) == cleanup_denied
    assert plan.directory.exists() == cleanup_denied
    assert target.with_suffix('.nfo').read_text() == 'correct metadata'
    await env.jobs.prepare_recovery()
    assert (await env.jobs.get_job(job.id)).status == ScrapeJobStatus.SUCCESS
    assert (await env.history.get_record(record.id)).status == TaskStatus.SUCCESS
    assert journal.read_records(job.id) == []


@pytest.mark.asyncio
async def test_shutdown_requeues_only_uncommitted_running_job(recovery_env, monkeypatch):
    env = recovery_env
    job, record = await running_job(env)
    started = asyncio.Event()

    async def paused(*args):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(jobs_module, '_run_scrape_job', paused)
    monkeypatch.setattr(jobs_module, '_workers_stopping', True)
    task = asyncio.create_task(jobs_module._execute_scrape_job(env.jobs, job.id))
    try:
        await asyncio.wait_for(started.wait(), 5)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert (await env.jobs.get_job(job.id)).status == ScrapeJobStatus.PENDING
    assert (await env.history.get_record(record.id)).status == TaskStatus.FAILED
    assert job.id in await env.jobs.prepare_recovery()


@pytest.mark.asyncio
async def test_shutdown_reset_losing_to_commit_does_not_modify_history(recovery_env):
    env = recovery_env
    job, record = await running_job(env)
    await env.jobs.update_job(job.id, status=ScrapeJobStatus.SUCCESS)
    await env.history.update_record(record.id, status=TaskStatus.SUCCESS)
    assert not await env.jobs.reset_job_to_pending(job.id)
    assert (await env.jobs.get_job(job.id)).status == ScrapeJobStatus.SUCCESS
    assert (await env.history.get_record(record.id)).status == TaskStatus.SUCCESS


@pytest.mark.asyncio
@pytest.mark.parametrize('registered', [False, True])
async def test_stale_recovery_writes_cannot_update_or_reinsert_successor_output(recovery_env, registered):
    env = recovery_env
    old, record = await running_job(env)
    await env.jobs.update_job(old.id, status=ScrapeJobStatus.SUCCESS)
    await env.history.update_record(record.id, status=TaskStatus.SUCCESS)
    new = await env.jobs.create_job(ScrapeJobCreate(
        file_path=str(env.source), output_dir=str(env.output),
        continuation_history_id=record.id, replaces_job_id=old.id, operation='reorganize',
    ))
    await env.jobs.update_job(new.id, status=ScrapeJobStatus.SUCCESS, history_record_id=record.id)
    await env.history.update_record(record.id, status=TaskStatus.SUCCESS, folder_path='new output')
    payload = dict(history_record_id=record.id, source_path=str(env.source),
                   target_path=str(env.output / 'new.mkv'), tmdb_id=123,
                   season=1, episode=2, title='New title')
    if registered:
        await env.scraped.register_output(**payload)
    before = await env.scraped.list_by_history_record(record.id)
    assert not await env.history.update_record(
        record.id, status=TaskStatus.PENDING_ACTION, folder_path='obsolete output', expected_job_id=old.id,
    )
    await env.scraped.register_output(**{**payload, 'target_path': str(env.output / 'obsolete.mkv')},
                                     replace_existing=True, expected_job_id=old.id)
    assert await env.scraped.list_by_history_record(record.id) == before
    current = await env.history.get_record(record.id)
    assert current.folder_path == 'new output' and current.status == TaskStatus.SUCCESS
    assert (await env.jobs.get_job(new.id)).status == ScrapeJobStatus.SUCCESS


@pytest.mark.asyncio
async def test_superseded_metadata_cleanup_does_not_replay_old_metadata(recovery_env):
    env = recovery_env
    _, _, job, record, target, plan = await committed_correction(env)
    # A new writer legitimately replaces the media and its shared metadata.
    target.write_bytes(b'new media')
    target.with_suffix('.nfo').write_text('new metadata')
    await env.jobs.update_job(job.id, status=ScrapeJobStatus.REPLACED, replaced_by_job_id='successor')
    await reconcile_file_operations(env.jobs, job.id)
    assert target.read_bytes() == b'new media'
    assert target.with_suffix('.nfo').read_text() == 'new metadata'
    assert (await env.jobs.get_job(job.id)).status == ScrapeJobStatus.REPLACED
    assert (await env.history.get_record(record.id)).status == TaskStatus.SUCCESS
    assert not plan.directory.exists()
    assert journal.read_records(job.id) == []


@pytest.mark.asyncio
async def test_superseded_stage_is_retained_without_changing_successor(recovery_env):
    env = recovery_env
    job, record = await running_job(env)
    staged = env.source.with_name('.mhti-rescrape-staged.mkv')
    token = journal.owner.set(job.id)
    try:
        with journal.operation_kind('stage'):
            publish_file(env.source, staged, OrganizeMode.MOVE)
    finally:
        journal.owner.reset(token)
    await env.jobs.update_job(job.id, status=ScrapeJobStatus.REPLACED, replaced_by_job_id='successor')
    await reconcile_file_operations(env.jobs, job.id)
    assert staged.read_bytes() == b'original video'
    assert not env.source.exists()
    assert journal.read_records(job.id)
    after = await env.jobs.get_job(job.id)
    assert after.status == ScrapeJobStatus.REPLACED and after.cleanup_error
    assert (await env.history.get_record(record.id)).status == TaskStatus.RUNNING
