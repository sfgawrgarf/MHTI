"""Public API and delayed credential results missed by the main review."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from sse_starlette.sse import AppStatus

from server.api.deps import AuthContext, get_history_service, get_scraper_service, require_auth
from server.api.v1 import history as history_api, scraper as scraper_api
from server.application.scraping.service import ScraperService
from server.domain.identity.session_service import session_service
from server.domain.metadata.tmdb_service import TMDBService
from server.domain.system.config_service import ConfigService
from server.infrastructure import realtime
from server.models.history import ScrapeLogStep
from server.models.scraper import BatchScrapeResponse, ScrapeResult, ScrapeStatus


@pytest.mark.asyncio
@pytest.mark.parametrize('endpoint', ['status', 'file', 'batch'])
async def test_direct_scraper_routes_use_real_token_only_gate(temp_db, monkeypatch, endpoint):
    config = ConfigService(db_path=temp_db)
    await config.save_api_token('configured-token')
    await config.set_api_token_verified(True)
    # The real TMDBService deliberately has no legacy Cookie API.
    service = ScraperService(config, TMDBService(config), None, None, None, None, None, None)
    result = ScrapeResult(file_path='/media/input.mkv', status=ScrapeStatus.SUCCESS)
    monkeypatch.setattr(service, 'scrape_file', AsyncMock(return_value=result))
    monkeypatch.setattr(service, 'batch_scrape', AsyncMock(return_value=BatchScrapeResponse(
        total=1, success=1, failed=0, results=[result],
    )))
    app = FastAPI()
    app.include_router(scraper_api.router)
    app.dependency_overrides[require_auth] = lambda: AuthContext(username='admin', session_id='s1')
    app.dependency_overrides[get_scraper_service] = lambda: service
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
        if endpoint == 'status':
            response = await client.get('/api/scraper/status')
            assert response.json()['ready'] is True
        else:
            payload = {'file_path': '/media/input.mkv'} if endpoint == 'file' else {'file_paths': ['/media/input.mkv']}
            response = await client.post('/api/scraper/' + endpoint, json=payload)
        assert response.status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize('valid', [None, False])
async def test_real_config_gate_rejects_missing_or_invalid_token(temp_db, valid):
    from server.application.scraping.config_resolver import ScraperConfigResolver

    config = ConfigService(db_path=temp_db)
    if valid is not None:
        await config.save_api_token('invalid-token')
        await config.set_api_token_verified(valid)
    resolver = ScraperConfigResolver(config, TMDBService(config))
    ready, message = await resolver.check_config()
    assert not ready and 'API Token' in message


@pytest.mark.asyncio
@pytest.mark.parametrize('closure', ['revoke', 'expire'])
async def test_history_sse_closes_and_does_not_send_after_session_ends(monkeypatch, closure):
    # sse-starlette keeps this process-wide event; each pytest case has its
    # own event loop, unlike the single-loop production process.
    monkeypatch.setattr(AppStatus, 'should_exit_event', None)
    monkeypatch.setattr(AppStatus, 'should_exit', False)
    app = FastAPI()
    app.include_router(history_api.router)
    auth = AuthContext(username='admin', session_id='stream-session')
    app.dependency_overrides[require_auth] = lambda: auth
    queue = asyncio.Queue()
    history = SimpleNamespace(
        get_record=AsyncMock(return_value=SimpleNamespace(scrape_logs=[ScrapeLogStep(name='initial')])),
        subscribe_logs=AsyncMock(return_value=queue),
        unsubscribe_logs=lambda *_: None,
    )
    app.dependency_overrides[get_history_service] = lambda: history
    monkeypatch.setattr(realtime, '_manager', realtime.ConnectionManager())
    active = True

    async def session_active(*_args, **_kwargs):
        return active

    monkeypatch.setattr(session_service, 'is_session_active', session_active)
    first = asyncio.Event()
    disconnect = asyncio.Event()
    bodies = []

    async def receive():
        await disconnect.wait()
        return {'type': 'http.disconnect'}

    async def send(message):
        if message['type'] == 'http.response.body':
            bodies.append(message.get('body', b''))
            if b'initial' in bodies[-1]:
                first.set()

    task = asyncio.create_task(app({
        'type': 'http', 'asgi': {'version': '3.0', 'spec_version': '2.3'},
        'http_version': '1.1', 'method': 'GET', 'scheme': 'http',
        'path': '/api/history/record-1/logs/stream', 'raw_path': b'/api/history/record-1/logs/stream',
        'root_path': '', 'query_string': b'', 'headers': [],
        'client': ('127.0.0.1', 1234), 'server': ('test', 80),
    }, receive, send))
    try:
        await asyncio.wait_for(first.wait(), 2)
        if closure == 'revoke':
            # Exercise the real shared revocation boundary, also used by logout,
            # password changes, replay detection and eviction.
            await session_service.close_session_connections([auth.session_id])
        else:
            active = False
        await queue.put([ScrapeLogStep(name='after-session-ended')])
        await asyncio.wait_for(task, 2)
        assert b'after-session-ended' not in b''.join(bodies)
    finally:
        disconnect.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize('phase', ['verify', 'adult', 'save_adult'])
@pytest.mark.parametrize('replacement', ['replace', 'delete', 'same_token'])
async def test_late_tmdb_probe_cannot_write_into_new_credential_generation(
    temp_db, monkeypatch, phase, replacement,
):
    config = ConfigService(db_path=temp_db)
    await config.save_api_token('token-A')
    tmdb = TMDBService(config)
    entered, release = asyncio.Event(), asyncio.Event()

    async def delayed(*_args, **_kwargs):
        entered.set()
        await release.wait()
        return (False, 'old result')

    monkeypatch.setattr(tmdb, 'verify_api_token', delayed if phase == 'verify' else AsyncMock(return_value=(True, None)))
    monkeypatch.setattr(tmdb, 'check_adult_access', delayed if phase != 'verify' else AsyncMock(return_value=(False, 'old result')))
    task = asyncio.create_task(
        tmdb.save_and_verify_api_token('token-A') if phase == 'save_adult' else tmdb.refresh_token_status()
    )
    try:
        await asyncio.wait_for(entered.wait(), 2)
        if replacement == 'delete':
            await config.delete_api_token()
        else:
            await config.save_api_token('token-A' if replacement == 'same_token' else 'token-B')
            await config.set_api_token_verified(True)
            await config.save_adult_status(True, 'new result')
        release.set()
        await task
        valid, _ = await config.get_api_token_verification()
        adult, message, _ = await config.get_adult_status()
        if replacement == 'delete':
            assert valid is None and adult is None and message is None
        else:
            assert valid is True and adult is True and message == 'new result'
    finally:
        release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
