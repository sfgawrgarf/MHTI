"""Fault injection at publication boundaries and journal ownership checks."""

import asyncio
import json
from pathlib import Path

import pytest

from server.tests.application.test_recovery_regressions import recovery_env  # noqa: F401
from server.tests.application.test_audit_remediation import audit_env, record_for  # noqa: F401
from server.tests.application.test_scraper_service import FakeTMDB, _build_service, _patch_tail
from server.application.file_recovery import reconcile_file_operations
from server.infrastructure import file_journal as journal
from server.infrastructure.file_operations import publish_file
from server.models.history import TaskStatus
from server.models.organize import OrganizeMode
from server.models.scrape_job import ScrapeJobCreate, ScrapeJobStatus
from server.models.scraper import ScrapeByIdRequest, ScrapeStatus


async def running_job(env, **kwargs):
    job = await env.jobs.create_job(ScrapeJobCreate(file_path=str(env.source), output_dir=str(env.output), **kwargs))
    record = await record_for(env, str(env.source), job_id=job.id, status=TaskStatus.RUNNING)
    await env.jobs.update_job(job.id, status=ScrapeJobStatus.RUNNING, history_record_id=record.id)
    return job, record


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', list(OrganizeMode))
async def test_recover_publication_before_receipt_update(recovery_env, monkeypatch, mode):
    env = recovery_env
    job, record = await running_job(env)
    target = env.output / 'published.mkv'
    token = journal.owner.set(job.id)
    original = journal.published
    monkeypatch.setattr(journal, 'published', lambda _: (_ for _ in ()).throw(RuntimeError('process lost')))
    try:
        with pytest.raises(RuntimeError, match='process lost'):
            publish_file(env.source, target, mode)
    finally:
        journal.owner.reset(token)
        monkeypatch.setattr(journal, 'published', original)
    assert target.exists()
    assert journal.read_records(job.id)[0]['phase'] == 'prepared'
    pending = await env.jobs.prepare_recovery()
    assert job.id not in pending
    assert (await env.jobs.get_job(job.id)).status == ScrapeJobStatus.SUCCESS
    assert str(target) in (await env.history.get_record(record.id)).folder_path
    assert env.source.exists() == (mode != OrganizeMode.MOVE)
    assert target.read_bytes() == b'original video'
    assert journal.read_records(job.id) == []


@pytest.mark.asyncio
async def test_recovery_retains_replaced_destination(recovery_env):
    env = recovery_env
    job, record = await running_job(env)
    target = env.output / 'published.mkv'
    token = journal.owner.set(job.id)
    try:
        publish_file(env.source, target, OrganizeMode.COPY)
    finally:
        journal.owner.reset(token)
    replacement = target.with_suffix('.new')
    replacement.write_bytes(b'external writer')
    replacement.replace(target)
    await env.jobs.prepare_recovery()
    assert target.read_bytes() == b'external writer'
    assert env.source.read_bytes() == b'original video'
    assert (await env.jobs.get_job(job.id)).status == ScrapeJobStatus.PENDING_ACTION
    assert (await env.history.get_record(record.id)).status == TaskStatus.PENDING_ACTION
    assert journal.read_records(job.id)


@pytest.mark.asyncio
async def test_stage_recovery_never_clobbers_new_original(recovery_env):
    env = recovery_env
    job, _ = await running_job(env)
    stage = env.source.with_name('input.mhti-rescrape-123.mkv')
    token = journal.owner.set(job.id)
    try:
        with journal.operation_kind('stage'):
            publish_file(env.source, stage, OrganizeMode.MOVE)
    finally:
        journal.owner.reset(token)
    env.source.write_bytes(b'new source')
    await env.jobs.prepare_recovery()
    assert env.source.read_bytes() == b'new source'
    assert stage.read_bytes() == b'original video'
    assert (await env.jobs.get_job(job.id)).status == ScrapeJobStatus.PENDING_ACTION


@pytest.mark.asyncio
async def test_metadata_replay_and_cancelled_correction_rollback(recovery_env, monkeypatch):
    env = recovery_env
    old, old_record = await running_job(env)
    await env.jobs.update_job(old.id, status=ScrapeJobStatus.SUCCESS)
    await env.history.update_record(old_record.id, status=TaskStatus.SUCCESS)
    job, record = await running_job(env, operation='rematch', correction_history_id=old_record.id, replaces_job_id=old.id)
    target = env.output / 'Show' / 'Season 1' / 'S01E10.mkv'
    target.parent.mkdir(parents=True)
    nfo = target.with_suffix('.nfo')
    nfo.write_text('original NFO')
    token = journal.owner.set(job.id)
    try:
        plan = journal.MetadataPlan.create(target, None)
        staged_nfo = plan.content / 'Show' / 'Season 1' / nfo.name
        staged_nfo.write_text('corrected NFO')
        plan.prepare()
        publish_file(env.source, target, OrganizeMode.COPY)
        # The ownership signature is durable even if acknowledgement is lost.
        original = journal.published
        monkeypatch.setattr(journal, 'published', lambda _: (_ for _ in ()).throw(RuntimeError('lost acknowledgement')))
        with pytest.raises(RuntimeError):
            plan.commit()
        monkeypatch.setattr(journal, 'published', original)
    finally:
        journal.owner.reset(token)
    assert nfo.read_text() == 'corrected NFO'
    await env.jobs.update_job(job.id, status=ScrapeJobStatus.CANCELLED)
    await reconcile_file_operations(env.jobs, job.id)
    assert nfo.read_text() == 'original NFO'
    assert target.read_bytes() == b'original video'
    assert (await env.history.get_record(old_record.id)).status == TaskStatus.SUCCESS
    assert str(target) in (await env.history.get_record(record.id)).folder_path
    assert journal.read_records(job.id) == []


@pytest.mark.asyncio
async def test_partial_metadata_commit_resumes_without_moving_video_again(recovery_env):
    env = recovery_env
    job, record = await running_job(env)
    target = env.output / 'Show' / 'Season 1' / 'S01E10.mkv'
    token = journal.owner.set(job.id)
    try:
        plan = journal.MetadataPlan.create(target, None)
        season = plan.content / 'Show' / 'Season 1'
        (season / 'S01E10.nfo').write_text('episode')
        (plan.content / 'Show' / 'tvshow.nfo').write_text('series')
        plan.prepare()
        publish_file(env.source, target, OrganizeMode.MOVE)
        # A restarted process reconstructs this plan from JSON, not memory.
        first = plan.record['files'][0]
        from server.infrastructure.file_journal import _metadata_write
        state = _metadata_write.set((plan.record, first))
        try:
            publish_file(Path(first['source']), Path(first['destination']), OrganizeMode.COPY)
        finally:
            _metadata_write.reset(state)
    finally:
        journal.owner.reset(token)
    await env.jobs.prepare_recovery()
    assert target.with_suffix('.nfo').read_text() == 'episode'
    assert (target.parent.parent / 'tvshow.nfo').read_text() == 'series'
    assert (await env.jobs.get_job(job.id)).status == ScrapeJobStatus.SUCCESS
    assert str(target) in (await env.history.get_record(record.id)).folder_path
    assert journal.read_records(job.id) == []


@pytest.mark.asyncio
async def test_metadata_conflict_keeps_published_video_addressable(recovery_env):
    env = recovery_env
    job, record = await running_job(env)
    target = env.output / 'Show' / 'Season 1' / 'S01E10.mkv'
    token = journal.owner.set(job.id)
    try:
        plan = journal.MetadataPlan.create(target, None)
        (plan.content / 'Show' / 'Season 1' / 'S01E10.nfo').write_text('my metadata')
        plan.prepare()
        publish_file(env.source, target, OrganizeMode.MOVE)
    finally:
        journal.owner.reset(token)
    target.with_suffix('.nfo').write_text('external metadata')
    await env.jobs.prepare_recovery()
    assert target.with_suffix('.nfo').read_text() == 'external metadata'
    assert str(target) in (await env.history.get_record(record.id)).folder_path
    assert (await env.jobs.get_job(job.id)).status == ScrapeJobStatus.PENDING_ACTION
    assert journal.read_records(job.id)


def test_metadata_rollback_preserves_other_writers_and_existing_artwork(tmp_path):
    target = tmp_path / 'out' / 'Show' / 'Season 1' / 'S01E10.mkv'
    target.parent.mkdir(parents=True)
    nfo = target.with_suffix('.nfo')
    nfo.write_text('old')
    extra = target.parent.parent / 'extrafanart' / '001.jpg'
    extra.parent.mkdir()
    extra.write_bytes(b'existing art')
    poster = target.parent / 'season01-poster.jpg'
    poster.write_bytes(b'existing poster')
    plan = journal.MetadataPlan.create(target, None)
    assert (plan.content / 'Show' / 'extrafanart' / '001.jpg').read_bytes() == b'existing art'
    assert (plan.content / 'Show' / 'Season 1' / poster.name).read_bytes() == b'existing poster'
    (plan.content / 'Show' / 'Season 1' / nfo.name).write_text('new')
    plan.prepare()
    plan.commit()
    external = nfo.with_suffix('.external')
    external.write_text('other writer')
    external.replace(nfo)
    plan.rollback()
    assert nfo.read_text() == 'other writer'
    assert extra.read_bytes() == b'existing art'
    assert poster.read_bytes() == b'existing poster'


@pytest.mark.asyncio
async def test_parallel_rename_metadata_uses_each_reserved_name(temp_db, tmp_path, monkeypatch):
    service = _build_service(temp_db, FakeTMDB())
    _patch_tail(monkeypatch, service)
    sources = [tmp_path / 'one.mp4', tmp_path / 'two.mp4']
    for index, source in enumerate(sources):
        source.write_bytes(str(index).encode())
    results = await asyncio.gather(*(service.scrape_by_id(ScrapeByIdRequest(
        file_path=str(source), output_dir=str(tmp_path / 'out'), tmdb_id=123,
        season=1, episode=1, link_mode=OrganizeMode.COPY, file_action='rename',
    )) for source in sources))
    assert all(result.status == ScrapeStatus.SUCCESS for result in results)
    assert len({result.dest_path for result in results}) == 2
    for index, result in enumerate(results):
        assert Path(result.dest_path).read_bytes() == str(index).encode()
        assert Path(result.nfo_path).stem == Path(result.dest_path).stem
        assert Path(result.nfo_path).exists()
    assert not list((tmp_path / 'out').rglob('.mhti-metadata-*'))


def test_journal_rejects_forged_identity_and_symlinks(tmp_path):
    record = journal.new_record('stage', source='/tmp/a', destination='/tmp/b')
    path = journal.root() / f"{record['id']}.json"
    content = json.loads(path.read_text())
    content['id'] = 'a' * 32
    path.write_text(json.dumps(content))
    with pytest.raises(journal.RecoveryConflict, match='身份'):
        journal.read_records()
    path.unlink()
    unrelated = tmp_path / 'unrelated.json'
    unrelated.write_text('{}')
    path.symlink_to(unrelated)
    with pytest.raises(journal.RecoveryConflict, match='软链接'):
        journal.read_records()
    assert unrelated.read_text() == '{}'


@pytest.mark.asyncio
async def test_repeated_cancellation_finishes_history_and_file_receipt(recovery_env, monkeypatch):
    from types import SimpleNamespace
    from server.tests.application.test_recovery_regressions import bind_worker
    from server.application import scrape_job_service as jobs
    env = recovery_env
    committed, wait_for_cancel, terminal_write = asyncio.Event(), asyncio.Event(), asyncio.Event()
    target = env.output / 'published.mkv'

    async def scrape(request, **kwargs):
        publish_file(env.source, target, OrganizeMode.MOVE)
        committed.set()
        await wait_for_cancel.wait()

    original = env.history.update_record
    blocked = False

    async def interrupted_terminal_write(*args, **kwargs):
        nonlocal blocked
        if kwargs.get('status') == TaskStatus.CANCELLED and not blocked:
            blocked = True
            terminal_write.set()
            await wait_for_cancel.wait()
        return await original(*args, **kwargs)

    bind_worker(env, monkeypatch, SimpleNamespace(scrape_file=scrape))
    monkeypatch.setattr(env.history, 'update_record', interrupted_terminal_write)
    job = await env.jobs.create_job(ScrapeJobCreate(file_path=str(env.source), output_dir=str(env.output)))
    task = asyncio.create_task(jobs._execute_scrape_job(env.jobs, job.id))
    try:
        await asyncio.wait_for(committed.wait(), 5)
        task.cancel()
        await asyncio.wait_for(terminal_write.wait(), 5)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        updated = await env.jobs.get_job(job.id)
        record = await env.history.get_record(updated.history_record_id)
        assert updated.status == ScrapeJobStatus.CANCELLED
        assert record.status == TaskStatus.CANCELLED
        assert str(target) in record.folder_path
        assert target.read_bytes() == b'original video'
        assert journal.read_records(job.id) == []
    finally:
        wait_for_cancel.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def test_metadata_cleanup_rejects_replaced_staging_directory(tmp_path):
    target = tmp_path / 'out' / 'Show' / 'Season 1' / 'S01E01.mkv'
    plan = journal.MetadataPlan.create(target, None)
    moved = plan.directory.with_name(plan.directory.name + '-original')
    plan.directory.rename(moved)
    plan.directory.mkdir()
    unrelated = plan.directory / 'keep.txt'
    unrelated.write_text('other writer')
    with pytest.raises(journal.RecoveryConflict, match='替换'):
        plan.cleanup()
    assert unrelated.read_text() == 'other writer'
    assert moved.exists()


@pytest.mark.asyncio
async def test_colocated_metadata_discovery_keeps_shared_deletion_rules(recovery_env):
    from server.models.organize import OrganizeConfig
    env = recovery_env
    await env.config.save_organize_config(OrganizeConfig(organize_dir=str(env.output)))
    target = env.output / 'Show' / 'Season 1' / 'S01E10.mkv'
    target.parent.mkdir(parents=True)
    target.write_bytes(b'video')
    own = target.with_suffix('.nfo')
    own.write_text('episode')
    season = target.parent / 'season.nfo'
    season.write_text('season')
    series = target.parent.parent / 'tvshow.nfo'
    series.write_text('series')
    record = await record_for(env, f'{env.source} => {target}')
    listing = await env.files.list_files(record.id)
    assert {str(own), str(season), str(series)} <= {item.path for item in listing.files}
    target.with_name('S01E11.mkv').write_bytes(b'other episode')
    listing = await env.files.list_files(record.id)
    assert str(own) in {item.path for item in listing.files}
    assert {str(season), str(series)}.isdisjoint({item.path for item in listing.files})
    backup_listing = await env.files.list_files(record.id, include_shared_metadata=True)
    assert {str(season), str(series)} <= {item.path for item in backup_listing.files}
