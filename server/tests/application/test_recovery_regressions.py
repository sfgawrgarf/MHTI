"""Faults found after PR #83: actual files, worker cancellation and process loss."""

import asyncio
import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from server.tests.application.test_audit_remediation import audit_env  # noqa: F401
from server.tests.application.test_audit_remediation import record_for
from server.tests.application.test_scraper_service import FakeTMDB, _build_service, _patch_tail
from server.application import scrape_job_service as jobs_module
from server.application import correction_service
from server.application.file_io import run_file_io
from server.application.history_actions import HistoryScrapeActions
from server.application.scheduler_service import SchedulerService
from server.infrastructure.file_operations import publish_file
from server.models.history import TaskStatus, HistoryConflictType
from server.models.organize import OrganizeConfig, OrganizeMode
from server.models.scrape_job import ScrapeJobCreate, ScrapeJobStatus
from server.models.scraper import ScrapeByIdRequest, ScrapeResult, ScrapeStatus


@pytest_asyncio.fixture
async def recovery_env(audit_env, monkeypatch):
    env = audit_env
    monkeypatch.setenv('MHTI_FILE_JOURNAL_DIR', str(env.source.parent / 'journal'))
    monkeypatch.setattr(correction_service, 'DATA_DIR', env.source.parent)
    monkeypatch.setattr(correction_service, 'BACKUP_ROOT', env.source.parent / 'backups')
    return env


def bind_worker(env, monkeypatch, scraper):
    monkeypatch.setattr('server.application.history_service.HistoryService', lambda **_: env.history)
    monkeypatch.setattr('server.application.scraped_file_service.ScrapedFileService', lambda **_: env.scraped)
    monkeypatch.setattr('server.domain.system.config_service.ConfigService', lambda **_: env.config)
    monkeypatch.setattr('server.bootstrap.get_scraper_service', lambda: scraper)


@pytest.mark.asyncio
async def test_cancel_after_move_commit_keeps_destination_in_history(recovery_env, monkeypatch):
    env = recovery_env
    target = env.source.parent / 'published.mkv'
    committed, release = threading.Event(), threading.Event()

    def move_then_pause():
        publish_file(env.source, target, OrganizeMode.MOVE)
        committed.set()
        assert release.wait(5)

    async def scrape(request, **kwargs):
        await run_file_io(move_then_pause)
        return ScrapeResult(file_path=request.file_path, dest_path=str(target), status=ScrapeStatus.SUCCESS)

    bind_worker(env, monkeypatch, SimpleNamespace(scrape_file=scrape))
    job = await env.jobs.create_job(ScrapeJobCreate(file_path=str(env.source), output_dir=str(env.output)))
    task = asyncio.create_task(jobs_module._execute_scrape_job(env.jobs, job.id))
    jobs_module._active_job_tasks[job.id] = task
    try:
        assert await asyncio.to_thread(committed.wait, 5)
        cancellation = asyncio.create_task(env.jobs.cancel_job(job.id))
        await asyncio.sleep(0.02)
        release.set()
        await cancellation
        updated = await env.jobs.get_job(job.id)
        record = await env.history.get_record(updated.history_record_id)
        assert str(target) in record.folder_path
        listing = await env.files.list_files(record.id)
        assert any(item.path == str(target) and item.exists for item in listing.files)
        assert target.read_bytes() == b'original video'
    finally:
        release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        jobs_module._active_job_tasks.pop(job.id, None)


@pytest.mark.asyncio
async def test_abrupt_process_loss_after_rescrape_stage_is_recovered(recovery_env):
    env = recovery_env
    target = env.output / 'Show' / 'Season 1' / 'S01E10.mkv'
    target.parent.mkdir(parents=True)
    target.write_bytes(b'only surviving video')
    env.source.unlink()
    old = await env.jobs.create_job(ScrapeJobCreate(
        file_path=str(env.source), output_dir=str(env.output), link_mode=OrganizeMode.COPY,
    ))
    await env.jobs.update_job(old.id, status=ScrapeJobStatus.SUCCESS)
    record = await record_for(env, f'{env.source} => {target}', job_id=old.id)
    actions = HistoryScrapeActions(env.history, env.jobs, env.scraped, env.files)
    queued = await actions.queue_record_operation(record.id, operation='reorganize')
    marker = env.source.parent / 'staged-path.txt'
    script = '''
import asyncio, os, sys
from pathlib import Path
from types import SimpleNamespace
from server.application.scrape_job_service import ScrapeJobService, _execute_scrape_job
import server.bootstrap
async def stop_after_preparation(request, **kwargs):
    Path(sys.argv[3]).write_text(request.file_path)
    os._exit(23)
server.bootstrap.get_scraper_service = lambda: SimpleNamespace(reorganize_with_metadata=stop_after_preparation)
asyncio.run(_execute_scrape_job(ScrapeJobService(db_path=Path(sys.argv[1])), sys.argv[2]))
'''
    child = await asyncio.to_thread(
        subprocess.run, [sys.executable, '-c', script, str(env.db), queued['job_id'], str(marker)],
        cwd=Path(__file__).resolve().parents[3], env=os.environ.copy(), capture_output=True, timeout=20,
    )
    assert child.returncode == 23, child.stderr.decode()
    staged = Path(marker.read_text())
    assert staged.exists() and not target.exists()
    recovered = await env.jobs.prepare_recovery()
    assert queued['job_id'] in recovered
    assert target.exists(), 'startup must restore the uncommitted rescrape input'
    assert target.read_bytes() == b'only surviving video'
    assert not staged.exists()


@pytest.mark.asyncio
async def test_correction_backup_includes_colocated_metadata(recovery_env):
    env = recovery_env
    target = env.output / 'Show' / 'Season 1' / 'S01E10.mkv'
    target.parent.mkdir(parents=True)
    target.write_bytes(b'video')
    nfo, thumb = target.with_suffix('.nfo'), target.with_suffix('.jpg')
    nfo.write_text('original NFO')
    thumb.write_bytes(b'original thumb')
    await env.config.save_organize_config(OrganizeConfig(organize_dir=str(env.output)))
    old = await env.jobs.create_job(ScrapeJobCreate(file_path=str(env.source), output_dir=str(env.output)))
    await env.jobs.update_job(old.id, status=ScrapeJobStatus.SUCCESS)
    record = await record_for(env, f'{env.source} => {target}', job_id=old.id)
    job = await env.jobs.create_job(ScrapeJobCreate(
        file_path=str(target), output_dir=str(env.output), operation='rematch',
        correction_history_id=record.id, replaces_job_id=old.id,
    ))
    directory = await correction_service.prepare_correction_backup(job, db_path=env.db)
    manifest = json.loads((directory / 'manifest.json').read_text())
    assert {str(nfo), str(thumb)} <= {item['source'] for item in manifest['files']}


@pytest.mark.asyncio
async def test_destination_is_not_reselected_after_metadata_preparation(temp_db, tmp_path, monkeypatch):
    source, output = tmp_path / 'source.mp4', tmp_path / 'out'
    source.write_bytes(b'my video')
    service = _build_service(temp_db, FakeTMDB())
    _patch_tail(monkeypatch, service)
    original = service._write_local_metadata_only
    competing = []

    async def prepare_then_compete(**kwargs):
        result = await original(**kwargs)
        target = Path(kwargs['dest_path_override'])
        target.write_bytes(b'other writer')
        competing.append(target)
        return result

    monkeypatch.setattr(service, '_write_local_metadata_only', prepare_then_compete)
    result = await service.scrape_by_id(ScrapeByIdRequest(
        file_path=str(source), output_dir=str(output), tmdb_id=123, season=1, episode=1,
        link_mode=OrganizeMode.COPY, file_action='rename',
    ))
    assert result.status == ScrapeStatus.FILE_CONFLICT
    assert source.exists()
    assert competing[0].read_bytes() == b'other writer'
    assert not competing[0].with_suffix('.nfo').exists(), 'losing a publication race must not modify another output'
    assert len(list(output.rglob('*.mp4'))) == 1


@pytest.mark.asyncio
async def test_retry_file_conflict_remains_actionable(recovery_env, monkeypatch):
    env = recovery_env
    record = await record_for(env, str(env.source), status=TaskStatus.FAILED)
    actions = HistoryScrapeActions(env.history, env.jobs, env.scraped, env.files)
    queued = await actions.queue_record_operation(record.id, ScrapeByIdRequest(
        file_path=str(env.source), output_dir=str(env.output), tmdb_id=123, season=1, episode=10,
    ))
    fake = SimpleNamespace(scrape_by_id=AsyncMock(return_value=ScrapeResult(
        file_path=str(env.source), dest_path=str(env.output / 'other.mkv'),
        status=ScrapeStatus.FILE_CONFLICT, message='target exists', selected_id=123,
        parsed_season=1, parsed_episode=10,
    )))
    bind_worker(env, monkeypatch, fake)
    await jobs_module._execute_scrape_job(env.jobs, queued['job_id'])
    updated = await env.history.get_record(record.id)
    assert updated.status == TaskStatus.PENDING_ACTION
    assert updated.conflict_type == HistoryConflictType.FILE_CONFLICT
    assert updated.conflict_data['tmdb_id'] == 123
    assert (await env.jobs.get_job(queued['job_id'])).status == ScrapeJobStatus.PENDING_ACTION


@pytest.mark.asyncio
async def test_backup_cleanup_error_does_not_block_scheduler(temp_db, monkeypatch):
    service = SchedulerService(db_path=temp_db)
    service.tick_once = AsyncMock()
    monkeypatch.setattr(correction_service, 'cleanup_expired_correction_backups', AsyncMock(side_effect=PermissionError('read only backup')))

    async def stop_after_tick(*args):
        raise asyncio.CancelledError

    monkeypatch.setattr('server.application.scheduler_service.asyncio.sleep', stop_after_tick)
    with pytest.raises(asyncio.CancelledError):
        await service._run()
    service.tick_once.assert_awaited_once()
