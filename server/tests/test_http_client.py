"""Connection reuse, configuration isolation and shutdown without network IO."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from server.infrastructure.http import close_http_clients, shared_http_client


@pytest.mark.asyncio
async def test_pool_reuses_connections_and_closes_on_shutdown(monkeypatch):
    factory = MagicMock()
    contexts = []

    def make_client(**kwargs):
        context = MagicMock(is_closed=False)
        context.__aenter__ = AsyncMock(return_value=context)
        context.__aexit__ = AsyncMock()
        contexts.append(context)
        return context

    factory.side_effect = make_client
    monkeypatch.setattr('server.infrastructure.http.httpx.AsyncClient', factory)
    async with shared_http_client(proxy='socks5://one', timeout=30) as first:
        pass
    async with shared_http_client(proxy='socks5://one', timeout=30) as second:
        assert first is second
    async with shared_http_client(proxy='socks5://two', timeout=30) as other_proxy:
        assert first is not other_proxy
    async with shared_http_client(proxy='socks5://one', timeout=60) as other_timeout:
        assert first is not other_timeout
    assert factory.call_count == 3
    await close_http_clients()
    for context in contexts:
        context.__aexit__.assert_awaited_once()


def test_pool_does_not_share_clients_between_event_loops(monkeypatch):
    created = []

    def make_client(**kwargs):
        context = MagicMock(is_closed=False)
        context.__aenter__ = AsyncMock(return_value=context)
        context.__aexit__ = AsyncMock()
        created.append(context)
        return context

    monkeypatch.setattr('server.infrastructure.http.httpx.AsyncClient', make_client)

    async def run():
        async with shared_http_client() as client:
            return client

    with asyncio.Runner() as first, asyncio.Runner() as second:
        assert first.run(run()) is not second.run(run())
        first.run(close_http_clients())
        second.run(close_http_clients())
    assert len(created) == 2
