"""Cross-boundary regression cases from the post-release main review."""

from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import aiosqlite
import pytest

from server.api.v1.history import _build_scrape_request
from server.application.history_actions import HistoryScrapeActions
from server.application.media_identity_service import MediaIdentityService
from server.application.watcher_service import P115EventStrategy
from server.infrastructure.db import db_connection
from server.models.history import HistoryConflictType, TaskStatus
from server.models.manual_job import ManualJobStatus
from server.models.organize import OrganizeMode
from server.models.scrape_job import ScrapeJobCreate, ScrapeJobSource, ScrapeJobStatus
from server.models.watcher import WatchedFolder, WatcherMode
from server.tests.application.test_audit_remediation import audit_env  # noqa: F401
from server.tests.application.test_manual_job_progress import batch, child, outcome


@pytest.mark.asyncio
@pytest.mark.parametrize('action', ['overwrite', 'rename'])
@pytest.mark.parametrize('source_exists', [True, False])
async def test_conflict_after_reorganize_queues_real_input(audit_env, action, source_exists):
    env = audit_env
    target = env.output / 'Show' / 'Season 1' / 'episode.mkv'
    target.parent.mkdir(parents=True)
    target.write_bytes(b'published media')
    job = await env.jobs.create_job(ScrapeJobCreate(file_path=str(env.source), output_dir=str(env.output)))
    await env.jobs.update_job(job.id, status=ScrapeJobStatus.PENDING_ACTION)
    from server.models.history import HistoryRecordCreate
    record = await env.history.create_record(HistoryRecordCreate(
        task_name='reorganize conflict', folder_path=f'{env.source} => {target}',
        status=TaskStatus.PENDING_ACTION, total_files=1, success_count=0,
        failed_count=0, duration_seconds=0, scrape_job_id=job.id,
    ))
    await env.history.update_record(record.id, conflict_type=HistoryConflictType.FILE_CONFLICT)
    if not source_exists:
        env.source.unlink()
    request = _build_scrape_request(
        await env.history.get_record(record.id), tmdb_id=123, season=1, episode=1,
        output_dir=str(env.output), metadata_dir=None, link_mode=OrganizeMode.COPY,
        file_action=action,
    )
    result = await HistoryScrapeActions(env.history, env.jobs, env.scraped, env.files).queue_scrape_and_update(record.id, request)
    queued = await env.jobs.get_job(result['job_id'])
    assert queued.file_path == str(env.source if source_exists else target)
    assert queued.file_action == action
    assert target.read_bytes() == b'published media'


@pytest.mark.asyncio
@pytest.mark.parametrize('before_days', [None, 1])
async def test_clearing_history_preserves_batch_outcomes_and_undo(audit_env, monkeypatch, before_days):
    env = audit_env
    service, parent = await batch(env, monkeypatch, dispatched=2, total=2)
    for index in range(2):
        job = await child(env, parent)
        record = await outcome(env, parent, job, TaskStatus.SUCCESS)
        await env.jobs.update_job(job.id, status=ScrapeJobStatus.SUCCESS, history_record_id=record.id)
        if index == 0:
            async with aiosqlite.connect(env.db) as db:
                await db.execute('UPDATE history_records SET executed_at = ? WHERE id = ?',
                                 ((datetime.now() - timedelta(days=3)).isoformat(), record.id))
                await db.commit()
    assert (await service.get_job(parent.id)).success_count == 2
    for _ in range(2):
        assert await env.history.clear_records(before_days) == (2 if before_days is None else 1)
        current = await service.get_job(parent.id)
        assert current.status == ManualJobStatus.SUCCESS
        assert (current.success_count, current.error_count, current.skip_count) == (2, 0, 0)
        assert (await env.history.undo_last()).undone
        assert (await service.get_job(parent.id)).success_count == 2


@pytest.mark.asyncio
async def test_clearing_dispatch_skip_history_keeps_batch_complete(audit_env, monkeypatch):
    service, parent = await batch(audit_env, monkeypatch)
    assert await service._record_skipped_file(parent.id, str(audit_env.source))
    assert (await service.get_job(parent.id)).skip_count == 1
    await audit_env.history.clear_records()
    current = await service.get_job(parent.id)
    assert current.status == ManualJobStatus.SUCCESS and current.skip_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('restarts', [1, 2])
async def test_recovery_attempts_count_one_logical_child(audit_env, monkeypatch, restarts):
    env = audit_env
    service, parent = await batch(env, monkeypatch)
    job = await child(env, parent)
    for _ in range(restarts):
        assert await env.jobs.claim_job(job.id)
        record = await outcome(env, parent, job, TaskStatus.RUNNING)
        await env.jobs.update_job(job.id, history_record_id=record.id)
        assert job.id in await env.jobs.prepare_recovery()
    assert await env.jobs.claim_job(job.id)
    record = await outcome(env, parent, job, TaskStatus.SUCCESS)
    await env.jobs.update_job(job.id, status=ScrapeJobStatus.SUCCESS, history_record_id=record.id)
    current = await service.get_job(parent.id)
    assert current.status == ManualJobStatus.SUCCESS
    assert (current.success_count, current.error_count) == (1, 0)


@pytest.mark.asyncio
@pytest.mark.parametrize('deletion', ['api', 'external', 'keep'])
async def test_watcher_dedup_requires_a_surviving_output(audit_env, monkeypatch, deletion):
    env = audit_env
    target = env.output / 'Show' / 'Season 1' / 'episode.mkv'
    target.parent.mkdir(parents=True)
    target.write_bytes(env.source.read_bytes())
    from server.models.history import HistoryRecordCreate
    record = await env.history.create_record(HistoryRecordCreate(
        task_name='published copy', folder_path=f'{env.source} => {target}',
        status=TaskStatus.SUCCESS, total_files=1, success_count=1, failed_count=0, duration_seconds=0,
    ))
    await env.scraped.register_output(history_record_id=record.id, source_path=str(env.source),
                                     target_path=str(target), tmdb_id=123, season=1, episode=1, title='Show')

    @asynccontextmanager
    async def context():
        async with db_connection(env.db) as db:
            yield db

    monkeypatch.setattr('server.application.media_identity_service.db_context', context)
    await MediaIdentityService().record(file_path=str(env.source), target_path=str(target),
                                       tmdb_id=123, season=1, episode=1, title='Show')
    if deletion == 'api':
        assert (await env.files.delete_files(record.id, 'organized')).deleted > 0
        async with aiosqlite.connect(env.db) as db:
            assert (await (await db.execute('SELECT COUNT(*) FROM media_versions')).fetchone())[0] == 0
    elif deletion == 'external':
        target.unlink()
    job = await env.jobs.create_job(ScrapeJobCreate(
        file_path=str(env.source), output_dir=str(env.output), source=ScrapeJobSource.WATCHER,
    ))
    assert (job is None) == (deletion == 'keep')


@pytest.mark.asyncio
async def test_115_receive_files_event_reaches_watched_queue(monkeypatch):
    folder = WatchedFolder(id='event', path='/115网盘/in', provider='115',
                           mode=WatcherMode.EVENT, file_id='root')
    detected = []
    strategy = P115EventStrategy(folder, lambda path, _folder: detected.append(path))
    strategy._running = True
    strategy._last_update_time = 1
    strategy._watched_dir_ids = {'root'}
    strategy._directory_paths = {'root': folder.path}
    strategy._loop_count = 1

    async def life_list(*_args, **_kwargs):
        strategy._running = False
        return {'state': True, 'data': {'list': [{
            'update_time': 2, 'behavior_type': 'receive_files',
            'items': [{'file_id': 'new-file', 'file_name': 'episode.mkv', 'ico': 'mkv',
                       'parent_id': 'root', 'file_size': 123}],
        }]}}

    async def no_sleep(_delay):
        return None

    from types import SimpleNamespace
    monkeypatch.setattr(strategy, '_get_client', AsyncMock(return_value=SimpleNamespace(life_list=life_list)))
    monkeypatch.setattr('server.application.watcher_service.asyncio.sleep', no_sleep)
    await strategy._event_loop()
    assert detected == ['/115网盘/in/episode.mkv']
