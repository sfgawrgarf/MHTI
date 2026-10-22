"""Review-only regressions against main 7a643fe; assertions describe desired behavior."""

import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import aiosqlite
import pytest
from fastapi import WebSocketDisconnect

from server.api.deps import AuthContext
from server.api.v1 import websocket as ws_module
from server.api.v1.config import save_proxy_config
from server.application.history_actions import HistoryScrapeActions
from server.application.scheduler_service import SchedulerService
from server.application.watcher_service import PendingFile, WatcherService
from server.infrastructure.db import create_all_tables
from server.models.config import ProxyConfig, ProxyConfigRequest
from server.models.history import TaskStatus
from server.models.manual_job import ManualJobAdvancedSettings
from server.models.scheduler import ScheduledTaskCreate
from server.models.scrape_job import ScrapeJobCreate, ScrapeJobStatus
from server.models.scraper import ScrapeByIdRequest
from server.models.watcher import WatchedFolderCreate, WatchedFolderUpdate
from server.tests.application.test_audit_remediation import audit_env, record_for  # noqa: F401


@pytest.mark.asyncio
async def test_frequent_pings_do_not_postpone_session_revalidation(monkeypatch):
    """Exercise the endpoint and monitor together, not just the monitor in isolation."""
    manager = AsyncMock()
    manager.disconnect = lambda *_: None
    active = AsyncMock(return_value=False)
    monkeypatch.setattr(ws_module, 'get_ws_manager', lambda: manager)
    monkeypatch.setattr(ws_module, 'authenticate_access_token',
                        AsyncMock(return_value=AuthContext('admin', 'expired-after-connect')))
    monkeypatch.setattr('server.domain.identity.session_service.session_service.is_session_active', active)

    class Socket:
        query_params = {'token': 'initially-valid'}

        def __init__(self):
            self.until = asyncio.get_running_loop().time() + 12

        async def receive_text(self):
            await asyncio.sleep(0.2)
            if asyncio.get_running_loop().time() >= self.until:
                raise WebSocketDisconnect()
            return '{"type":"ping"}'

    await asyncio.wait_for(ws_module.websocket_endpoint(Socket()), timeout=16)
    assert active.await_count >= 1, 'Frequent pings suppressed all session-expiry checks'
    assert manager.close_client.await_count >= 1


@pytest.mark.asyncio
async def test_scheduler_recovers_from_one_failed_finish_write(temp_db, monkeypatch):
    async with aiosqlite.connect(temp_db) as db:
        await create_all_tables(db)
        await db.commit()
    service = SchedulerService(db_path=temp_db)
    scheduled = await service.create_task(ScheduledTaskCreate(
        name='audit', folder_path='/media', cron_expression='* * * * *'))
    now = datetime.now() + timedelta(minutes=2)
    monkeypatch.setattr(service, '_dispatch', AsyncMock())
    finish = service._repo.finish_run
    attempts = 0

    async def transient_failure(*args):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise aiosqlite.OperationalError('database is locked')
        return await finish(*args)

    monkeypatch.setattr(service._repo, 'finish_run', transient_failure)
    for offset in range(3):
        try:
            await service.tick_once(now + timedelta(seconds=offset))
        except aiosqlite.OperationalError:
            pass  # Mirrors _run: log and continue at the next tick.
    result = await service.get_task(scheduled.id)
    assert result.last_status != 'running', 'A transient finish failure stranded the schedule'


@pytest.mark.asyncio
@pytest.mark.parametrize('action', ['disable', 'delete'])
async def test_changed_watcher_does_not_dispatch_stale_pending_files(temp_db, tmp_path, monkeypatch, action):
    service = WatcherService(db_path=temp_db)
    folder = await service.create_folder(WatchedFolderCreate(path=str(tmp_path), file_stable_seconds=0))
    source = tmp_path / 'episode.mkv'
    source.write_bytes(b'media')
    service._pending_files[str(source)] = PendingFile(path=str(source), detected_at=0, folder=folder)
    dispatch = AsyncMock(return_value={str(source)})
    monkeypatch.setattr(service, '_create_jobs_for_files', dispatch)
    if action == 'disable':
        await service.update_folder(folder.id, WatchedFolderUpdate(enabled=False))
    else:
        await service.delete_folder(folder.id)
    await service._process_pending_once()
    dispatch.assert_not_awaited()


@pytest.mark.asyncio
async def test_proxy_blank_credentials_preserve_saved_authentication(audit_env):
    config = audit_env.config
    await config.save_proxy_config(ProxyConfig(
        type='http', host='proxy.example', port=8080, username='saved-user', password='saved-password'))
    await save_proxy_config(ProxyConfigRequest(
        type='http', host='proxy.example', port=8081, username=None, password=None), config)
    saved = await config.get_proxy_config()
    assert (saved.username, saved.password) == ('saved-user', 'saved-password')


@pytest.mark.asyncio
async def test_conflict_resolution_preserves_task_specific_settings(audit_env):
    env = audit_env
    custom = ManualJobAdvancedSettings(
        use_global_naming=False, episode_file_template='custom-{episode}',
        use_global_metadata=False, nfo_enabled=False, process_subtitle=False)
    old = await env.jobs.create_job(ScrapeJobCreate(
        file_path=str(env.source), output_dir=str(env.output), advanced_settings=custom))
    await env.jobs.update_job(old.id, status=ScrapeJobStatus.PENDING_ACTION)
    record = await record_for(env, str(env.source), job_id=old.id, status=TaskStatus.PENDING_ACTION)
    actions = HistoryScrapeActions(env.history, env.jobs, env.scraped, env.files)
    result = await actions.queue_scrape_and_update(record.id, ScrapeByIdRequest(
        file_path=str(env.source), tmdb_id=123, season=1, episode=10, output_dir=str(env.output)))
    queued = await env.jobs.get_job(result['job_id'])
    assert queued.advanced_settings == custom
