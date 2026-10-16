"""End-to-end boundaries missed by shallow mocks in the comprehensive audit."""

import asyncio
import json
import threading
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiosqlite
import pytest
import pytest_asyncio

from server.application import scrape_job_service as jobs_module
from server.application.history_actions import HistoryScrapeActions
from server.application.history_files import HistoryFileService
from server.application.history_service import HistoryService
from server.application.scraped_file_service import ScrapedFileService
from server.application.scheduler_service import SchedulerService
from server.application.watcher_service import CompatStrategy, P115EventStrategy, P115ScanStrategy, WatcherService
from server.domain.system.config_service import ConfigService
from server.infrastructure.db import configure_connection, create_all_tables
from server.models.history import HistoryRecordCreate, TaskStatus
from server.models.organize import OrganizeConfig
from server.models.scheduler import ScheduledTaskCreate
from server.models.scrape_job import ScrapeJobCreate, ScrapeJobStatus
from server.models.scraper import ScrapeByIdRequest
from server.models.watcher import WatchedFolder, WatcherMode


@pytest_asyncio.fixture
async def audit_env(temp_db, tmp_path, monkeypatch):
    async with aiosqlite.connect(temp_db) as db:
        await configure_connection(db)
        await create_all_tables(db)
        await db.commit()
    monkeypatch.setattr(jobs_module, '_ensure_worker', lambda: None)
    monkeypatch.setattr(jobs_module, '_scrape_queue', asyncio.Queue())
    monkeypatch.setattr(jobs_module, 'get_notifier', lambda: AsyncMock())
    monkeypatch.setattr('server.application.history_service.get_notifier', lambda: AsyncMock())
    source = tmp_path / 'input.mkv'
    source.write_bytes(b'original video')
    output = tmp_path / 'out'
    metadata = tmp_path / 'meta'
    config = ConfigService(db_path=temp_db)
    await config.save_organize_config(OrganizeConfig(organize_dir=str(output), metadata_dir=str(metadata)))
    history = HistoryService(db_path=temp_db)
    jobs = jobs_module.ScrapeJobService(db_path=temp_db)
    scraped = ScrapedFileService(db_path=temp_db)
    files = HistoryFileService(history, scraped, config, jobs)
    monkeypatch.setenv('MHTI_ALLOWED_MEDIA_ROOTS', str(tmp_path))
    return SimpleNamespace(db=temp_db, source=source, output=output, metadata=metadata,
                           history=history, jobs=jobs, scraped=scraped, files=files, config=config)


async def record_for(env, path, *, status=TaskStatus.SUCCESS, job_id=None):
    record = await env.history.create_record(HistoryRecordCreate(
        task_name='audit regression', folder_path=path, status=status,
        total_files=1, success_count=1, failed_count=0, duration_seconds=0,
        scrape_job_id=job_id,
    ))
    await env.history.update_record(record.id, title='Show', tmdb_id=123, season_number=1, episode_number=10)
    return await env.history.get_record(record.id)


@pytest.mark.asyncio
async def test_deleting_organized_symlink_preserves_source_and_other_episode(audit_env):
    env = audit_env
    season = env.output / 'Show' / 'Season 1'
    season.mkdir(parents=True)
    target = season / 'S01E10.mkv'
    target.symlink_to(env.source)
    metadata = env.metadata / 'Show' / 'Season 1'
    metadata.mkdir(parents=True)
    own = metadata / 'S01E10.nfo'
    own.write_text('own')
    other = metadata / 'S01E100.nfo'
    other.write_text('other episode')
    shared = metadata.parent / 'tvshow.nfo'
    shared.write_text('shared')
    record = await record_for(env, f'{env.source} => {target}')
    result = await env.files.delete_files(record.id, 'organized')
    assert any(item.deleted and item.path == str(target) for item in result.results)
    assert env.source.read_bytes() == b'original video'
    assert not target.is_symlink()
    assert not own.exists()
    assert other.read_text() == 'other episode'
    assert shared.read_text() == 'shared'


@pytest.mark.asyncio
async def test_record_retry_is_reserved_once_and_cancelled_in_real_worker(audit_env, monkeypatch):
    env = audit_env
    old = await env.jobs.create_job(ScrapeJobCreate(file_path=str(env.source), output_dir=str(env.output)))
    await env.jobs.update_job(old.id, status=ScrapeJobStatus.FAILED)
    record = await record_for(env, str(env.source), status=TaskStatus.FAILED, job_id=old.id)
    actions = HistoryScrapeActions(env.history, env.jobs, env.scraped, env.files)
    request = ScrapeByIdRequest(file_path=str(env.source), tmdb_id=123, season=1, episode=10)
    results = await asyncio.gather(*(
        actions.queue_record_operation(record.id, request.model_copy()) for _ in range(2)
    ), return_exceptions=True)
    queued = [value for value in results if isinstance(value, dict)]
    assert len(queued) == 1
    job_id = queued[0]['job_id']
    assert (await env.history.get_record(record.id)).scrape_job_id == job_id
    entered = asyncio.Event()
    cancelled = asyncio.Event()

    async def hanging_scrape(*args, **kwargs):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr('server.bootstrap.get_scraper_service', lambda: SimpleNamespace(scrape_by_id=hanging_scrape))
    execution = asyncio.create_task(jobs_module._execute_scrape_job(env.jobs, job_id))
    jobs_module._active_job_tasks[job_id] = execution
    try:
        await asyncio.wait_for(entered.wait(), 5)
        updated, did_cancel, _ = await env.jobs.cancel_job(job_id)
        assert did_cancel and updated.status == ScrapeJobStatus.CANCELLED
        assert cancelled.is_set()
        assert (await env.history.get_record(record.id)).status == TaskStatus.CANCELLED
        assert env.source.exists()
    finally:
        execution.cancel()
        await asyncio.gather(execution, return_exceptions=True)
        jobs_module._active_job_tasks.pop(job_id, None)


@pytest.mark.asyncio
async def test_parent_cancellation_covers_more_than_500_children(audit_env):
    env = audit_env
    await env.jobs._ensure_db()
    async with aiosqlite.connect(env.db) as db:
        await db.executemany(
            'INSERT INTO scrape_jobs (id,file_path,output_dir,source,source_id,status,created_at) '
            'VALUES (?, ?, ?, ?, ?, ?, ?)',
            [(f'child-{i}', f'/file-{i}', '/out', 'manual', 42, 'pending', datetime.now().isoformat())
             for i in range(503)],
        )
        await db.commit()
    assert await env.jobs.cancel_jobs_by_source(42) == 503
    assert await env.jobs._repo.get_active_ids_by_source(42) == []


@pytest.mark.asyncio
async def test_correction_replaces_only_on_commit_and_creates_real_backup(audit_env, monkeypatch):
    from server.application import correction_service as backup

    env = audit_env
    old = await env.jobs.create_job(ScrapeJobCreate(file_path=str(env.source), output_dir=str(env.output)))
    await env.jobs.update_job(old.id, status=ScrapeJobStatus.SUCCESS)
    original = await record_for(env, str(env.source), job_id=old.id)
    correction = await env.jobs.create_job(ScrapeJobCreate(
        file_path=str(env.source), output_dir=str(env.output), operation='rematch',
        replaces_job_id=old.id, correction_history_id=original.id,
        correction_tmdb_id=456, correction_season=2, correction_episode=3,
    ))
    assert (await env.jobs.get_job(old.id)).status == ScrapeJobStatus.SUCCESS
    assert (await env.history.get_record(original.id)).status == TaskStatus.SUCCESS
    monkeypatch.setattr(backup, 'DATA_DIR', env.source.parent)
    monkeypatch.setattr(backup, 'BACKUP_ROOT', env.source.parent / 'backups')
    directory = await backup.prepare_correction_backup(correction, db_path=env.db)
    manifest = json.loads((directory / 'manifest.json').read_text())
    env.source.write_bytes(b'corrected')
    saved = directory / manifest['files'][0]['backup']
    assert saved.read_bytes() == b'original video'
    assert backup._cleanup_expired(manifest['expires_at'] - 1) == 0
    await env.jobs._repo.complete_correction(correction.id)
    assert (await env.jobs.get_job(old.id)).status == ScrapeJobStatus.REPLACED
    assert (await env.history.get_record(original.id)).status == TaskStatus.REPLACED
    assert backup._cleanup_expired(manifest['expires_at'] + 1) == 1


@pytest.mark.asyncio
async def test_scheduler_claim_retry_recovery_and_no_duplicate_dispatch(audit_env, monkeypatch):
    env = audit_env
    service = SchedulerService(db_path=env.db)
    task = await service.create_task(ScheduledTaskCreate(name='scan', folder_path=str(env.source.parent), cron_expression='0 * * * *'))
    now = datetime.now() + timedelta(hours=1)
    async with aiosqlite.connect(env.db) as db:
        await db.execute('UPDATE scheduled_tasks SET next_run=? WHERE id=?', (now.isoformat(), task.id))
        await db.commit()
    dispatch = AsyncMock(side_effect=RuntimeError('provider unavailable'))
    monkeypatch.setattr(service, '_dispatch', dispatch)
    await asyncio.gather(service.tick_once(now), service.tick_once(now))
    assert dispatch.await_count == 1
    state = await service.get_task(task.id)
    assert state.last_status == 'retry' and state.retry_count == 1
    dispatch.side_effect = None
    await service.tick_once(now + timedelta(seconds=60))
    assert dispatch.await_count == 2
    state = await service.get_task(task.id)
    assert state.last_status == 'success' and state.last_run is not None
    claimed = await service._repo.claim_due(state.next_run)
    assert claimed is not None
    await service._repo.recover_running(state.next_run)
    recovered = await service._repo.claim_due(state.next_run)
    assert recovered['running_for'] == claimed['running_for']


@pytest.mark.asyncio
async def test_scheduler_dispatch_is_idempotent_after_restart(audit_env, monkeypatch):
    from server.application import manual_job_service as manual

    monkeypatch.setattr(manual, '_ensure_worker', lambda: None)
    env = audit_env
    service = SchedulerService(db_path=env.db)
    claimed = {'id': 'schedule', 'running_for': '2026-01-01T00:00:00', 'folder_path': str(env.source.parent)}
    await service._dispatch(claimed)
    await service._dispatch(claimed)
    async with aiosqlite.connect(env.db) as db:
        cursor = await db.execute('SELECT count(*) FROM manual_jobs WHERE schedule_key=?', ('schedule:2026-01-01T00:00:00',))
        assert (await cursor.fetchone())[0] == 1


@pytest.mark.asyncio
async def test_watcher_scan_keeps_size_for_new_cloud_files(monkeypatch):
    folder = WatchedFolder(id='cloud', path='/115网盘/in', provider='115', mode=WatcherMode.COMPAT, file_id='root')
    detected = []
    strategy = P115ScanStrategy(folder, lambda path, folder: detected.append(path))
    strategy._running = True
    strategy._p115_service = SimpleNamespace(scan_folder=AsyncMock(return_value=[{
        'path': '/115网盘/in/new.mkv', 'file_id': 'new', 'parent_id': 'root', 'size': 200_000_000,
    }]))
    calls = 0

    async def next_tick(*args):
        nonlocal calls
        calls += 1
        if calls > 1:
            raise asyncio.CancelledError

    monkeypatch.setattr('server.application.watcher_service.asyncio.sleep', next_tick)
    await strategy._scan_loop()
    assert detected == ['/115网盘/in/new.mkv']
    assert strategy.detected_meta[detected[0]]['size'] == 200_000_000


def test_cloud_events_keep_ancestor_paths_and_do_not_guess():
    folder = WatchedFolder(id='cloud', path='/115网盘/in', provider='115', mode=WatcherMode.EVENT, file_id='root')
    detected = []
    strategy = P115EventStrategy(folder, lambda path, folder: detected.append(path))
    strategy._watched_dir_ids = {'a', 'b', 'unknown'}
    strategy._directory_paths = {'a': '/115网盘/in/A/Season 1', 'b': '/115网盘/in/B/Season 1'}
    for parent in ('a', 'b', 'unknown'):
        strategy._process_event_item({'file_id': parent, 'file_name': 'E01.mkv', 'ico': 'mkv', 'parent_id': parent})
    assert detected == ['/115网盘/in/A/Season 1/E01.mkv', '/115网盘/in/B/Season 1/E01.mkv']
    assert 'unknown' not in strategy._processed_file_ids


@pytest.mark.asyncio
async def test_watcher_initial_and_compat_scan_use_io_thread(tmp_path, monkeypatch):
    from server.domain.media.file_service import FileService

    loop_thread = threading.get_ident()
    calls = []

    def scan(*args):
        calls.append(threading.get_ident())
        return []

    monkeypatch.setattr(FileService, 'scan_folder', scan)
    folder = WatchedFolder(id='local', path=str(tmp_path), mode=WatcherMode.COMPAT)
    strategy = CompatStrategy(folder, lambda *_: None)
    await strategy._init_known_files()
    watcher = WatcherService()
    watcher._running = True
    monkeypatch.setattr(jobs_module.ScrapeJobService, 'get_pending_file_paths', AsyncMock(return_value=set()))
    monkeypatch.setattr(watcher._repo, 'update_last_scan', AsyncMock())
    await watcher._initial_scan([folder])
    assert len(calls) == 2
    assert all(thread != loop_thread for thread in calls)


@pytest.mark.asyncio
async def test_corrected_identity_moves_fingerprint_to_new_episode(audit_env, monkeypatch):
    from contextlib import asynccontextmanager
    from server.application import media_identity_service as identity

    env = audit_env

    @asynccontextmanager
    async def connection():
        async with aiosqlite.connect(env.db) as db:
            db.row_factory = aiosqlite.Row
            yield db

    monkeypatch.setattr(identity, 'db_context', connection)
    service = identity.MediaIdentityService()
    await service.record(file_path=str(env.source), target_path='/old', tmdb_id=1, season=1, episode=1, title='Old')
    await service.record(file_path=str(env.source), target_path='/new', tmdb_id=2, season=3, episode=4, title='New')
    async with connection() as db:
        cursor = await db.execute('SELECT identity_key,source_path,target_path FROM media_versions')
        rows = await cursor.fetchall()
    assert [tuple(row) for row in rows] == [('tmdb:2:s03:e04', str(env.source), '/new')]


@pytest.mark.asyncio
async def test_correction_context_survives_conflict_resolution(audit_env):
    env = audit_env
    old = await env.jobs.create_job(ScrapeJobCreate(file_path=str(env.source), output_dir=str(env.output)))
    await env.jobs.update_job(old.id, status=ScrapeJobStatus.SUCCESS)
    original = await record_for(env, str(env.source), job_id=old.id)
    correction = await env.jobs.create_job(ScrapeJobCreate(
        file_path=str(env.source), output_dir=str(env.output), operation='rematch',
        replaces_job_id=old.id, correction_history_id=original.id,
    ))
    await env.jobs.update_job(correction.id, status=ScrapeJobStatus.PENDING_ACTION)
    pending = await record_for(env, str(env.source), status=TaskStatus.PENDING_ACTION, job_id=correction.id)
    actions = HistoryScrapeActions(env.history, env.jobs, env.scraped, env.files)
    result = await actions.queue_scrape_and_update(pending.id, ScrapeByIdRequest(
        file_path=str(env.source), tmdb_id=456, season=2, episode=3,
        output_dir=str(env.output), file_action='rename',
    ))
    continuation = await env.jobs.get_job(result['job_id'])
    assert continuation.operation == 'rematch'
    assert continuation.correction_history_id == original.id
    assert continuation.replaces_job_id == old.id
    assert continuation.link_mode.value == 'copy'
    assert (await env.jobs.get_job(old.id)).status == ScrapeJobStatus.SUCCESS
    assert (await env.history.get_record(original.id)).status == TaskStatus.SUCCESS


@pytest.mark.asyncio
async def test_failed_correction_retry_keeps_original_replacement_context(audit_env):
    env = audit_env
    old = await env.jobs.create_job(ScrapeJobCreate(file_path=str(env.source), output_dir=str(env.output)))
    await env.jobs.update_job(old.id, status=ScrapeJobStatus.SUCCESS)
    original = await record_for(env, str(env.source), job_id=old.id)
    correction = await env.jobs.create_job(ScrapeJobCreate(
        file_path=str(env.source), output_dir=str(env.output), operation='rematch',
        replaces_job_id=old.id, correction_history_id=original.id,
    ))
    await env.jobs.update_job(correction.id, status=ScrapeJobStatus.FAILED)
    failed = await record_for(env, str(env.source), status=TaskStatus.FAILED, job_id=correction.id)
    actions = HistoryScrapeActions(env.history, env.jobs, env.scraped, env.files)
    result = await actions.queue_record_operation(failed.id, ScrapeByIdRequest(
        file_path=str(env.source), tmdb_id=456, season=2, episode=3,
    ))
    retry = await env.jobs.get_job(result['job_id'])
    assert retry.operation == 'rematch'
    assert retry.replaces_job_id == old.id and retry.correction_history_id == original.id
    assert (await env.jobs.get_job(old.id)).status == ScrapeJobStatus.SUCCESS


@pytest.mark.asyncio
async def test_record_worker_enforces_timeout(audit_env, monkeypatch):
    env = audit_env
    record = await record_for(env, str(env.source), status=TaskStatus.FAILED)
    actions = HistoryScrapeActions(env.history, env.jobs, env.scraped, env.files)
    queued = await actions.queue_record_operation(record.id, ScrapeByIdRequest(
        file_path=str(env.source), tmdb_id=123, season=1, episode=10,
    ))

    async def hang(*args, **kwargs):
        await asyncio.Event().wait()

    monkeypatch.setattr(ConfigService, 'get_system_config', AsyncMock(return_value=SimpleNamespace(task_timeout=1)))
    monkeypatch.setattr('server.bootstrap.get_scraper_service', lambda: SimpleNamespace(scrape_by_id=hang))
    await jobs_module._execute_scrape_job(env.jobs, queued['job_id'])
    assert (await env.jobs.get_job(queued['job_id'])).status == ScrapeJobStatus.TIMEOUT
    assert (await env.history.get_record(record.id)).status == TaskStatus.TIMEOUT
    assert env.source.exists()


@pytest.mark.asyncio
async def test_reorganize_uses_worker_and_stored_metadata_without_tmdb(audit_env, monkeypatch):
    from server.tests.application.test_scraper_service import FakeTMDB, _build_service, _patch_tail

    env = audit_env
    old = await env.jobs.create_job(ScrapeJobCreate(file_path=str(env.source), output_dir=str(env.output)))
    await env.jobs.update_job(old.id, status=ScrapeJobStatus.SUCCESS)
    record = await record_for(env, str(env.source), job_id=old.id)
    actions = HistoryScrapeActions(env.history, env.jobs, env.scraped, env.files)
    queued = await actions.queue_record_operation(record.id, operation='reorganize')
    tmdb = FakeTMDB()
    scraper = _build_service(env.db, tmdb)
    _patch_tail(monkeypatch, scraper)
    monkeypatch.setattr('server.bootstrap.get_scraper_service', lambda: scraper)
    await jobs_module._execute_scrape_job(env.jobs, queued['job_id'])
    assert (await env.jobs.get_job(queued['job_id'])).status == ScrapeJobStatus.SUCCESS
    assert (await env.history.get_record(record.id)).status == TaskStatus.SUCCESS
    tmdb.get_series_by_api.assert_not_awaited()
    tmdb.get_season_by_api.assert_not_awaited()
    assert list(env.output.rglob('*.mkv'))
