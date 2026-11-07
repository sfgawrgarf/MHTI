"""Cross-flow audit regressions against the unchanged v2.1.11 main tree."""

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import aiosqlite
import httpx
import pytest
from fastapi import HTTPException
from starlette.requests import Request

from server.api.v1 import auth as auth_api
from server.application import manual_job_service as manual_module
from server.application.history_actions import HistoryScrapeActions
from server.domain.identity.auth_service import AuthService
from server.domain.identity.session_service import SessionService
from server.domain.integration.emby_service import EmbyService
from server.infrastructure.db import configure_connection
from server.models.auth import AuthConfig, LoginRequest, RegisterRequest
from server.models.emby import EmbyConfig, ConflictCheckRequest, ConflictType
from server.models.history import TaskStatus
from server.models.manual_job import ManualJobStatus
from server.models.scrape_job import ScrapeJobCreate, ScrapeJobSource, ScrapeJobStatus
from server.tests.application.test_audit_remediation import audit_env, record_for  # noqa: F401
from server.tests.application.test_manual_job_progress import batch


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['login', 'register'])
async def test_password_change_rejects_login_validated_before_change(audit_env, monkeypatch, operation):
    env = audit_env
    auth = AuthService()
    auth._config_cache = AuthConfig(jwt_secret='isolated-audit-secret-at-least-thirty-two-bytes')
    sessions = SessionService()

    class Manager:
        @asynccontextmanager
        async def get_connection(self):
            async with aiosqlite.connect(env.db) as db:
                await configure_connection(db)
                db.row_factory = aiosqlite.Row
                yield db

    async def manager():
        return Manager()

    monkeypatch.setattr('server.domain.identity.auth_service.get_db_manager', manager)
    monkeypatch.setattr('server.domain.identity.session_service.get_db_manager', manager)
    monkeypatch.setattr(sessions, '_get_max_sessions', AsyncMock(return_value=10))
    monkeypatch.setattr(sessions, 'close_session_connections', AsyncMock())
    monkeypatch.setattr(sessions, 'record_login', AsyncMock())
    monkeypatch.setattr(auth_api, 'auth_service', auth)
    monkeypatch.setattr(auth_api, 'session_service', sessions)
    if operation == 'login':
        await auth.register_admin('admin', 'old-password')
    async with aiosqlite.connect(env.db) as db:
        if operation == 'login':
            cursor = await db.execute('SELECT id FROM admin WHERE username = ?', ('admin',))
            user_id = (await cursor.fetchone())[0]
            await db.execute(
                "INSERT INTO sessions (id,user_id,refresh_token_hash,expires_at) VALUES ('current',?,'current-hash','2099-01-01T00:00:00+00:00')",
                (user_id,),
            )
        await db.commit()
    verified, release = asyncio.Event(), asyncio.Event()
    create = sessions.create_session

    async def delayed_create(**kwargs):
        verified.set()
        await release.wait()
        return await create(**kwargs)

    monkeypatch.setattr(sessions, 'create_session', delayed_create)
    request = Request({'type': 'http', 'headers': [], 'client': ('127.0.0.1', 1234)})
    pending = asyncio.create_task(auth_api.login(request, LoginRequest(username='admin', password='old-password'))
                                  if operation == 'login' else auth_api.register(request, RegisterRequest(username='admin', password='old-password')))
    try:
        await asyncio.wait_for(verified.wait(), 5)
        changed, _ = await auth.change_password('admin', 'old-password', 'new-password', except_session_id='current')
        assert changed
        release.set()
        try:
            response = await pending
        except HTTPException as exc:
            assert exc.status_code == 401
        else:
            assert not await sessions.is_session_active(response.session_id), 'stale credentials created a live session after password rotation'
    finally:
        release.set()
        await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['delete', 'reorganize_cleanup'])
async def test_obsolete_record_cannot_delete_new_owner_output(audit_env, operation):
    env = audit_env
    target = env.output / 'Show' / 'Season 1' / 'episode.mkv'
    target.parent.mkdir(parents=True)
    target.write_bytes(b'first publication')
    first = await record_for(env, f'{env.source} => {target}')
    await env.scraped.register_output(history_record_id=first.id, source_path=str(env.source),
                                     target_path=str(target), tmdb_id=123, season=1, episode=10, title='Show')
    other_source = env.source.with_name('new-version.mkv')
    other_source.write_bytes(b'new publication')
    second = await record_for(env, f'{other_source} => {target}')
    target.write_bytes(other_source.read_bytes())
    await env.scraped.register_output(history_record_id=second.id, source_path=str(other_source),
                                     target_path=str(target), tmdb_id=123, season=1, episode=10, title='Show')
    if operation == 'delete':
        await env.files.delete_files(first.id, 'organized')
    else:
        await HistoryScrapeActions(env.history, env.jobs, env.scraped, env.files).prepare_input(first)
    assert target.exists(), 'old history removed the newer publication'
    assert target.read_bytes() == b'new publication'
    assert await env.scraped.list_by_history_record(second.id)


@pytest.mark.asyncio
@pytest.mark.parametrize('status,operation', [(TaskStatus.FAILED, 'retry'), (TaskStatus.SUCCESS, 'reorganize')])
async def test_undo_clear_restores_usable_job_context(audit_env, status, operation):
    env = audit_env
    job = await env.jobs.create_job(ScrapeJobCreate(file_path=str(env.source), output_dir=str(env.output)))
    record = await record_for(env, str(env.source), status=status, job_id=job.id)
    await env.jobs.update_job(job.id, status=ScrapeJobStatus(status.value), history_record_id=record.id)
    assert await env.history.clear_records() == 1
    assert (await env.history.undo_last()).undone
    result = await HistoryScrapeActions(env.history, env.jobs, env.scraped, env.files).queue_record_operation(
        record.id, operation=operation,
    )
    assert result['queued']


@pytest.mark.asyncio
async def test_automatic_duplicate_skip_does_not_ban_watcher_recovery(audit_env, monkeypatch):
    env = audit_env
    original = await env.jobs.create_job(ScrapeJobCreate(file_path=str(env.source), output_dir=str(env.output)))
    service, parent = await batch(env, monkeypatch, dispatched=None)
    await service.update_job_status(parent.id, ManualJobStatus.PENDING)
    await manual_module._execute_job(service, parent.id)
    records, count = await env.history.list_records(manual_job_id=parent.id)
    assert count == 1 and records[0].status == TaskStatus.SKIPPED
    await env.jobs.update_job(original.id, status=ScrapeJobStatus.FAILED)
    recovered = await env.jobs.create_job(ScrapeJobCreate(
        file_path=str(env.source), output_dir=str(env.output), source=ScrapeJobSource.WATCHER,
    ))
    assert recovered is not None, 'automatic dispatch skip became a permanent user skip'


@pytest.mark.asyncio
async def test_emby_connection_test_rejects_invalid_api_key(audit_env, monkeypatch):
    service = EmbyService(audit_env.config)

    def response(request):
        if request.url.path == '/System/Info/Public':
            return httpx.Response(200, json={'ServerName': 'Emby', 'Version': '4'})
        return httpx.Response(401, json={'error': 'unauthorized'})

    monkeypatch.setattr(service, '_get_client', lambda _config: httpx.AsyncClient(
        base_url='https://emby.example', transport=httpx.MockTransport(response)))
    result = await service.test_connection_with_config(EmbyConfig(
        enabled=True, server_url='https://emby.example', api_key='invalid-key',
    ))
    assert not result.success, 'public endpoint success hid authenticated endpoint failure'


@pytest.mark.asyncio
async def test_emby_known_different_tmdb_id_is_not_the_same_series(audit_env, monkeypatch):
    service = EmbyService(audit_env.config)
    config = EmbyConfig(enabled=True, server_url='https://emby.example', api_key='key')

    def response(request):
        if request.url.params.get('IncludeItemTypes') == 'Series':
            return httpx.Response(200, json={'Items': [{'Id': 'old-show', 'Name': 'Same Title', 'ProviderIds': {'Tmdb': '200'}}]})
        return httpx.Response(200, json={'Items': [{'Id': 'old-episode', 'Name': 'Old episode', 'ParentIndexNumber': 1, 'IndexNumber': 1}]})

    monkeypatch.setattr(service, 'get_config', AsyncMock(return_value=config))
    monkeypatch.setattr(service, '_get_client', lambda _config: httpx.AsyncClient(
        base_url=config.server_url, transport=httpx.MockTransport(response)))
    result = await service.check_conflict(ConflictCheckRequest(series_name='Same Title', tmdb_id=100, season=1, episode=1))
    assert result.conflict_type == ConflictType.NO_CONFLICT, 'explicit different TMDB identity fell back to a same-name show'
