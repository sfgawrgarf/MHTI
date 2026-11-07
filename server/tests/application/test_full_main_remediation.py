"""Acceptance boundaries for the complete audit remediation."""

import asyncio
import io
import logging
from logging.handlers import RotatingFileHandler
from unittest.mock import AsyncMock

import aiosqlite
import pytest
from fastapi import HTTPException

from server.api.v1.ai import save_ai_config
from server.application import manual_job_service as manual_module
from server.application.manual_job_service import ManualJobService
from server.domain.system.log_service import LogService
from server.infrastructure.log_configuration import LoggingRuntime
from server.infrastructure.repositories.log_repository import LogRepository
from server.models.ai import AIConfig, AIConfigUpdate
from server.models.file import ScannedFile
from server.models.history import HistoryRecordCreate, TaskStatus
from server.models.log import LogConfigUpdate, LogLevel
from server.models.manual_job import LinkMode, ManualJobAdvancedSettings, ManualJobCreate
from server.models.organize import OrganizeMode
from server.models.scrape_job import ScrapeJobCreate, ScrapeJobStatus
from server.models.storage import StorageLocator, StorageProvider
from server.tests.application.test_audit_remediation import audit_env, record_for  # noqa: F401


@pytest.mark.asyncio
async def test_unregistered_replacement_is_preserved(audit_env):
    env = audit_env
    target = env.output / 'episode.mkv'
    target.parent.mkdir()
    target.write_bytes(b'original')
    record = await record_for(env, f'{env.source} => {target}')
    await env.scraped.register_output(history_record_id=record.id, source_path=str(env.source),
                                     target_path=str(target), tmdb_id=1, season=1, episode=1, title='Show')
    replacement = target.with_suffix('.tmp')
    replacement.write_bytes(b'new publication before its database registration')
    replacement.replace(target)
    listing = await env.files.list_files(record.id)
    assert not next(entry for entry in listing.files if entry.path == str(target)).deletable
    await env.files.delete_files(record.id, 'organized')
    assert target.read_bytes() == b'new publication before its database registration'


@pytest.mark.asyncio
async def test_undo_restores_cloud_locators_and_settings(audit_env):
    env = audit_env
    source = StorageLocator(provider=StorageProvider.P115, path='/115网盘/Inbox/a.mkv', file_id='123', is_dir=False)
    output = StorageLocator(provider=StorageProvider.P115, path='/115网盘/Library', file_id='456')
    job = await env.jobs.create_job(ScrapeJobCreate(file_path=source.path, file_locator=source,
        output_dir=output.path, output_locator=output, link_mode=OrganizeMode.COPY,
        advanced_settings=ManualJobAdvancedSettings(use_global_naming=False, episode_file_template='{title}')))
    record = await record_for(env, source.path, status=TaskStatus.FAILED, job_id=job.id)
    await env.jobs.update_job(job.id, status=ScrapeJobStatus.FAILED, history_record_id=record.id)
    before = await env.jobs.get_job(job.id)
    await env.history.clear_records()
    assert (await env.history.undo_last()).undone
    restored = await env.jobs.get_job(job.id)
    assert restored.file_locator == before.file_locator
    assert restored.output_locator == before.output_locator
    assert restored.advanced_settings == before.advanced_settings
    assert restored.status == before.status


@pytest.mark.asyncio
async def test_legacy_automatic_skip_does_not_block_watcher(audit_env):
    env = audit_env
    await env.history.create_record(HistoryRecordCreate(task_name='automatic duplicate', folder_path=str(env.source),
        status=TaskStatus.SKIPPED, total_files=1, success_count=0, failed_count=0, duration_seconds=0,
        error_message='已有处理中的刮削任务，本次未重复投递'))
    assert not await env.jobs._repo.has_skipped_history(str(env.source), None)
    await env.history.create_record(HistoryRecordCreate(task_name='user skip', folder_path=str(env.source),
        status=TaskStatus.SKIPPED, total_files=1, success_count=0, failed_count=0, duration_seconds=0))
    assert await env.jobs._repo.has_skipped_history(str(env.source), None)


@pytest.mark.asyncio
@pytest.mark.parametrize('new_url,reject', [('https://new.example/v1', True), ('https://old.example/v1/', False)])
async def test_ai_saved_credentials_stay_with_endpoint(new_url, reject):
    from types import SimpleNamespace
    provider = SimpleNamespace(get_config=AsyncMock(return_value=AIConfig(base_url='https://old.example/v1', api_key='private-key')),
                               save_config=AsyncMock())
    request = AIConfigUpdate(base_url=new_url)
    if reject:
        with pytest.raises(HTTPException) as caught:
            await save_ai_config(request, provider)
        assert caught.value.status_code == 422
        provider.save_config.assert_not_awaited()
    else:
        await save_ai_config(request, provider)
        assert provider.save_config.await_args.args[0].api_key == 'private-key'


@pytest.mark.asyncio
@pytest.mark.parametrize('cloud', [False, True])
async def test_selected_files_are_the_exact_durable_dispatch_set(audit_env, monkeypatch, cloud):
    env = audit_env
    monkeypatch.setattr(manual_module, '_ensure_worker', lambda: None)
    monkeypatch.setattr(manual_module, '_job_queue', asyncio.Queue())
    if cloud:
        path, target = '/115网盘/Inbox', '/115网盘/Library'
        locator = StorageLocator(provider=StorageProvider.P115, path=path, file_id='10')
        destination = StorageLocator(provider=StorageProvider.P115, path=target, file_id='20')
        file = ScannedFile(filename='selected.mkv', path=path + '/selected.mkv', file_id='30', parent_id='10', size=100, extension='.mkv')
    else:
        path, target = str(env.source.parent), str(env.output)
        locator = destination = None
        (env.source.parent / 'unselected.mkv').write_bytes(b'unselected')
        file = ScannedFile(filename=env.source.name, path=str(env.source), size=env.source.stat().st_size, extension='.mkv')
    service = ManualJobService(db_path=env.db)
    job = await service.create_job(ManualJobCreate(scan_path=path, target_folder=target, scan_locator=locator,
        target_locator=destination, link_mode=LinkMode.COPY, config_reuse_id='00123456', selected_files=[file],
        advanced_settings=ManualJobAdvancedSettings(use_global_organize=False, file_size_filter=0)))
    # Read the serialized job back, as restart recovery does.
    restored = await service.get_job(job.id)
    assert restored.config_reuse_id == '00123456'
    assert restored.selected_files == [file]
    await manual_module._execute_job(service, job.id)
    jobs, count = await env.jobs.list_jobs(source_id=job.id)
    assert count == 1
    assert jobs[0].file_path == file.path
    if cloud:
        assert jobs[0].file_locator.file_id == '30'


@pytest.mark.asyncio
async def test_home_summary_is_not_limited_to_fifty_records(audit_env):
    env = audit_env
    async with aiosqlite.connect(env.db) as db:
        await db.executemany('''INSERT INTO history_records
            (id, task_name, folder_path, executed_at, status, total_files, success_count, failed_count, duration_seconds)
            VALUES (?, 'summary', '/source', ?, ?, 1, 0, 0, 0)''',
            [(f'item-{index}', '2026-11-06T17:00:00+00:00' if index % 2 else '2026-11-07T01:00:00',
              'success' if index < 80 else 'failed') for index in range(100)])
        await db.commit()
    result = await env.history._repo.summary('2026-11-01', '2026-11-07', 480, 480)
    assert result['total'] == 100 and result['success'] == 80 and result['failed'] == 20
    assert result['days'] == {'2026-11-07': 100}


@pytest.mark.asyncio
async def test_saved_log_config_changes_real_output_handlers(audit_env, tmp_path):
    stream, database = io.StringIO(), io.StringIO()
    root = logging.Logger('isolated-runtime')
    console = logging.StreamHandler(stream)
    file = RotatingFileHandler(tmp_path / 'app.log')
    db = logging.StreamHandler(database)
    root.addHandler(console)
    root.addHandler(file)
    # The application DB handler is not a StreamHandler.
    runtime = LoggingRuntime(root, file, db)
    root.addHandler(db)
    service = LogService()
    service._repo = LogRepository(audit_env.db)
    try:
        await service.bind_runtime_configuration(runtime.apply)
        await service.update_config(LogConfigUpdate(log_level=LogLevel.WARNING, console_enabled=False,
            file_enabled=True, db_enabled=True, max_file_size_mb=3, max_file_count=2))
        root.info('below threshold')
        root.error('stored error')
        assert stream.getvalue() == '' and 'stored error' in database.getvalue()
        assert (tmp_path / 'app.log').read_text().strip() == 'stored error'
        assert file.maxBytes == 3 * 1024 * 1024 and file.backupCount == 2
        await service.update_config(LogConfigUpdate(log_level=LogLevel.DEBUG, console_enabled=True, file_enabled=False, db_enabled=False))
        root.debug('console only')
        assert 'console only' in stream.getvalue()
        assert 'console only' not in database.getvalue()
        assert 'console only' not in (tmp_path / 'app.log').read_text()
    finally:
        file.close()
