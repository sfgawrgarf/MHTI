"""Post-merge regressions: distinct seasons and interrupted recovery cleanup."""

import asyncio
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from server.tests.application.test_recovery_regressions import recovery_env  # noqa: F401
from server.tests.application.test_audit_remediation import audit_env, record_for  # noqa: F401
from server.tests.application.test_file_operation_recovery import running_job
from server.application import correction_service
from server.infrastructure import file_journal as journal
from server.infrastructure.file_operations import publish_file
from server.models.history import TaskStatus
from server.models.organize import OrganizeConfig, OrganizeMode
from server.models.scrape_job import ScrapeJobCreate, ScrapeJobStatus


@pytest.mark.asyncio
@pytest.mark.parametrize('colocated', [False, True])
@pytest.mark.parametrize('metadata_only', [False, True])
async def test_delete_episode_keeps_other_season_with_identical_filename(recovery_env, colocated, metadata_only):
    env = recovery_env
    metadata = env.output if colocated else env.metadata
    await env.config.save_organize_config(OrganizeConfig(
        organize_dir=str(env.output), metadata_dir='' if colocated else str(metadata),
    ))
    target = env.output / 'Show' / 'Season 1' / '01.mkv'
    other = env.output / 'Show' / 'Season 2' / '01.mkv'
    target.parent.mkdir(parents=True)
    other.parent.mkdir(parents=True)
    target.write_bytes(b'this episode')
    if not metadata_only:
        other.write_bytes(b'other season')
    own_nfo = metadata / 'Show' / 'Season 1' / '01.nfo'
    other_nfo = metadata / 'Show' / 'Season 2' / '01.nfo'
    own_nfo.parent.mkdir(parents=True, exist_ok=True)
    other_nfo.parent.mkdir(parents=True, exist_ok=True)
    own_nfo.write_text('this episode')
    other_nfo.write_text('other season')
    shared = metadata / 'Show' / 'tvshow.nfo'
    shared.write_text('shared series')
    poster = shared.with_name('poster.jpg')
    poster.write_bytes(b'shared poster')
    record = await record_for(env, f'{env.source} => {target}')
    await env.files.delete_files(record.id, 'organized')
    assert not target.exists() and not own_nfo.exists()
    assert other_nfo.read_text() == 'other season'
    assert shared.read_text() == 'shared series'
    assert poster.read_bytes() == b'shared poster'
    if not metadata_only:
        assert other.read_bytes() == b'other season'


@pytest.mark.asyncio
@pytest.mark.parametrize('cleanup_point', ['partial', 'directory_removed', 'empty_plan'])
async def test_recovery_finishes_metadata_cleanup_after_process_loss(recovery_env, cleanup_point):
    env = recovery_env
    job, record = await running_job(env)
    target = env.output / 'Show' / 'Season 1' / '01.mkv'
    token = journal.owner.set(job.id)
    try:
        plan = journal.MetadataPlan.create(target, None)
        if cleanup_point != 'empty_plan':
            (plan.content / 'Show' / 'Season 1' / '01.nfo').write_text('published metadata')
        plan.prepare()
        publish_file(env.source, target, OrganizeMode.MOVE)
        plan.commit()
    finally:
        journal.owner.reset(token)
    if cleanup_point == 'partial':
        Path(plan.record['files'][0]['source']).unlink()
    else:
        shutil.rmtree(plan.directory)
    # JSON is still present, as when cleanup is interrupted before forget().
    await env.jobs.prepare_recovery()
    assert (await env.jobs.get_job(job.id)).status == ScrapeJobStatus.SUCCESS
    assert (await env.history.get_record(record.id)).status == TaskStatus.SUCCESS
    assert target.read_bytes() == b'original video'
    if cleanup_point != 'empty_plan':
        assert target.with_suffix('.nfo').read_text() == 'published metadata'
    assert journal.read_records(job.id) == []
    assert not plan.directory.exists()
    await env.jobs.prepare_recovery()  # A second recovery is harmless.
    assert (await env.jobs.get_job(job.id)).status == ScrapeJobStatus.SUCCESS


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['copy', 'move'])
async def test_process_loss_during_copy_removes_only_owned_partial_payload(recovery_env, mode):
    env = recovery_env
    job, _ = await running_job(env)
    target = env.output / 'published.mkv'
    unrelated = env.output / '.mhti-publish-unrelated' / 'payload.tmp'
    unrelated.parent.mkdir(parents=True)
    unrelated.write_bytes(b'not this operation')
    script = '''
import errno, os, sys
from pathlib import Path
from server.infrastructure import file_journal, file_operations
from server.models.organize import OrganizeMode
file_journal.owner.set(sys.argv[3])
def interrupted_copy(source, destination, **kwargs):
    Path(destination).write_bytes(b'partial bytes')
    os._exit(31)
file_operations.shutil.copy2 = interrupted_copy
if sys.argv[4] == 'move':
    def cross_device(*args, **kwargs):
        raise OSError(errno.EXDEV, 'different filesystem')
    file_operations.os.link = cross_device
file_operations.publish_file(Path(sys.argv[1]), Path(sys.argv[2]), OrganizeMode(sys.argv[4]))
'''
    child = await asyncio.to_thread(
        subprocess.run, [sys.executable, '-c', script, str(env.source), str(target), job.id, mode],
        cwd=Path(__file__).resolve().parents[3], env=os.environ.copy(), capture_output=True, timeout=20,
    )
    assert child.returncode == 31, child.stderr.decode()
    assert len(list(env.output.glob('.mhti-publish-*'))) == 2
    pending = await env.jobs.prepare_recovery()
    assert job.id in pending
    assert env.source.read_bytes() == b'original video'
    assert unrelated.read_bytes() == b'not this operation'
    assert list(env.output.glob('.mhti-publish-*')) == [unrelated.parent]
    assert journal.read_records(job.id) == []


@pytest.mark.asyncio
async def test_correction_backs_up_series_metadata_without_season_directory(recovery_env):
    env = recovery_env
    target = env.output / 'Show' / 'Season 1' / '01.mkv'
    target.parent.mkdir(parents=True)
    target.write_bytes(b'video')
    series = env.metadata / 'Show'
    series.mkdir(parents=True)
    files = [series / 'tvshow.nfo', series / 'poster.jpg', series / 'extrafanart' / '001.jpg']
    for path in files:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'original artwork')
    assert not (series / 'Season 1').exists()
    old = await env.jobs.create_job(ScrapeJobCreate(file_path=str(env.source), output_dir=str(env.output), metadata_dir=str(env.metadata)))
    await env.jobs.update_job(old.id, status=ScrapeJobStatus.SUCCESS)
    record = await record_for(env, f'{env.source} => {target}', job_id=old.id)
    job = await env.jobs.create_job(ScrapeJobCreate(
        file_path=str(target), output_dir=str(env.output), metadata_dir=str(env.metadata),
        operation='rematch', correction_history_id=record.id, replaces_job_id=old.id,
    ))
    directory = await correction_service.prepare_correction_backup(job, db_path=env.db)
    manifest = json.loads((directory / 'manifest.json').read_text())
    assert {str(path) for path in files} <= {item['source'] for item in manifest['files']}
