"""Cloud-only isolated COPY trial with real workers, SQLite, NFO and file I/O.

TMDB responses and Emby checks use deterministic fixtures; no live credentials or
user media are accessed. GitHub Actions supplies a fresh directory per case.
"""

import asyncio
import os
from pathlib import Path
import subprocess
import sys

import pytest

from server.tests.application.test_recovery_regressions import recovery_env, bind_worker  # noqa: F401
from server.tests.application.test_audit_remediation import audit_env  # noqa: F401
from server.tests.application.test_scraper_service import FakeTMDB, _build_service, _patch_tail
from server.application import scrape_job_service as jobs_module
from server.application.scrape_job_service import ScrapeJobService
from server.models.history import TaskStatus
from server.models.organize import OrganizeMode
from server.models.scrape_job import ScrapeJobCreate, ScrapeJobStatus


def copy_request(env, source, **kwargs):
    return ScrapeJobCreate(file_path=str(source), output_dir=str(env.output), metadata_dir=str(env.metadata),
                           link_mode=OrganizeMode.COPY, correction_tmdb_id=123,
                           correction_season=1, correction_episode=1, **kwargs)


@pytest.mark.asyncio
async def test_isolated_copy_trial_scrape_correct_cancel_and_restart(recovery_env, monkeypatch):
    env = recovery_env
    tmdb = FakeTMDB()
    scraper = _build_service(env.db, tmdb)
    _patch_tail(monkeypatch, scraper)
    bind_worker(env, monkeypatch, scraper)
    initial = await env.jobs.create_job(copy_request(env, env.source))
    await jobs_module._execute_scrape_job(env.jobs, initial.id)
    done = await env.jobs.get_job(initial.id)
    assert done.status == ScrapeJobStatus.SUCCESS
    record = await env.history.get_record(done.history_record_id)
    registered = await env.scraped.list_by_history_record(record.id)
    target = Path(registered[0].target_path)
    assert target.read_bytes() == env.source.read_bytes() == b'original video'
    nfo = env.metadata / target.parent.parent.name / target.parent.name / target.with_suffix('.nfo').name
    assert nfo.exists()

    tmdb.get_season_by_api.return_value.episodes[0].overview = 'corrected plot in COPY trial'
    correction = await env.jobs.create_job(copy_request(
        env, target, operation='rematch', correction_history_id=record.id,
        replaces_job_id=initial.id, file_action='overwrite',
    ))
    await jobs_module._execute_scrape_job(env.jobs, correction.id)
    corrected = await env.jobs.get_job(correction.id)
    assert corrected.status == ScrapeJobStatus.SUCCESS
    assert corrected.output_committed_at and corrected.cleanup_completed_at
    assert (await env.history.get_record(record.id)).status == TaskStatus.REPLACED
    assert 'corrected plot in COPY trial' in nfo.read_text()
    assert target.read_bytes() == env.source.read_bytes() == b'original video'

    cancel_source = env.source.with_name('cancel-input.mkv')
    cancel_source.write_bytes(b'preserve cancelled source')
    prepared = asyncio.Event()
    pause = asyncio.Event()
    original = scraper._write_local_metadata_only
    async def pause_after_preparing_metadata(**kwargs):
        result = await original(**kwargs)
        prepared.set()
        await pause.wait()
        return result
    monkeypatch.setattr(scraper, '_write_local_metadata_only', pause_after_preparing_metadata)
    cancel = await env.jobs.create_job(copy_request(env, cancel_source, file_action='rename'))
    task = asyncio.create_task(jobs_module._execute_scrape_job(env.jobs, cancel.id))
    jobs_module._active_job_tasks[cancel.id] = task
    try:
        await asyncio.wait_for(prepared.wait(), 5)
        _, cancelled, _ = await env.jobs.cancel_job(cancel.id)
        assert cancelled
    finally:
        pause.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        jobs_module._active_job_tasks.pop(cancel.id, None)
    assert cancel_source.read_bytes() == b'preserve cancelled source'
    assert (await env.jobs.get_job(cancel.id)).status == ScrapeJobStatus.CANCELLED
    restarted = ScrapeJobService(db_path=env.db)
    assert await restarted.prepare_recovery() == []
    assert (await restarted.get_job(correction.id)).status == ScrapeJobStatus.SUCCESS
    assert target.read_bytes() == b'original video'
    assert 'corrected plot in COPY trial' in nfo.read_text()
    assert len(list(env.output.rglob('*.mkv'))) == 1
    assert not list(env.output.rglob('.mhti-*'))
    assert not list(env.metadata.rglob('.mhti-*'))


@pytest.mark.asyncio
async def test_isolated_copy_trial_process_exit_after_video_publication(recovery_env):
    env = recovery_env
    job = await env.jobs.create_job(copy_request(env, env.source))
    script = '''
import asyncio, os, sys
from pathlib import Path
from pytest import MonkeyPatch
from server.tests.application.test_scraper_service import FakeTMDB, _build_service, _patch_tail
from server.application.scrape_job_service import ScrapeJobService, _execute_scrape_job
import server.application.history_service as history
import server.application.scraped_file_service as scraped
import server.domain.system.config_service as config
import server.bootstrap as bootstrap
db = Path(sys.argv[1])
History, Scraped, Config = history.HistoryService, scraped.ScrapedFileService, config.ConfigService
scraper = _build_service(db, FakeTMDB())
_patch_tail(MonkeyPatch(), scraper)
history.HistoryService = lambda **kwargs: History(db_path=db)
scraped.ScrapedFileService = lambda **kwargs: Scraped(db_path=db)
config.ConfigService = lambda **kwargs: Config(db_path=db)
bootstrap.get_scraper_service = lambda: scraper
organize = scraper._organize_local_output
async def exit_after_publish(**kwargs):
    await organize(**kwargs)
    os._exit(41)
scraper._organize_local_output = exit_after_publish
asyncio.run(_execute_scrape_job(ScrapeJobService(db_path=db), sys.argv[2]))
'''
    child = await asyncio.to_thread(
        subprocess.run, [sys.executable, '-c', script, str(env.db), job.id],
        cwd=Path(__file__).resolve().parents[3], env=os.environ.copy(), capture_output=True, timeout=20,
    )
    assert child.returncode == 41, child.stderr.decode()
    assert env.source.read_bytes() == b'original video'
    restarted = ScrapeJobService(db_path=env.db)
    assert job.id not in await restarted.prepare_recovery()
    recovered = await restarted.get_job(job.id)
    assert recovered.status == ScrapeJobStatus.SUCCESS
    assert recovered.output_committed_at and recovered.cleanup_completed_at
    record = await env.history.get_record(recovered.history_record_id)
    assert record.status == TaskStatus.SUCCESS
    registered = await env.scraped.list_by_history_record(record.id)
    target = Path(registered[0].target_path)
    assert target.read_bytes() == env.source.read_bytes() == b'original video'
    nfo = env.metadata / target.parent.parent.name / target.parent.name / target.with_suffix('.nfo').name
    assert nfo.exists()
    assert str(target) in record.folder_path
    assert not list(env.output.rglob('.mhti-*'))
    assert not list(env.metadata.rglob('.mhti-*'))
