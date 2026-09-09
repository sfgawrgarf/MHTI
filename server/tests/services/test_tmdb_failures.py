"""TMDB transport failures must never masquerade as missing metadata."""

import asyncio
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from server.core.exceptions import (
    TMDBConnectionError,
    TMDBError,
    TMDBInvalidCredentialsError,
    TMDBNotConfiguredError,
    TMDBRateLimitError,
    TMDBTimeoutError,
)
from server.services.tmdb_service import TMDBService


@pytest.fixture
def transport(monkeypatch):
    config = SimpleNamespace(
        get_api_token=AsyncMock(return_value="private-api-key"),
        get_proxy_config=AsyncMock(return_value=SimpleNamespace(get_url=lambda: None)),
        get_system_config=AsyncMock(return_value=SimpleNamespace(task_timeout=30, retry_count=2)),
        get_language_config=AsyncMock(return_value=SimpleNamespace(primary="zh-CN")),
    )
    client = AsyncMock()
    factory = MagicMock()
    factory.return_value.__aenter__.return_value = client
    monkeypatch.setattr("server.services.tmdb_service.httpx.AsyncClient", factory)
    sleep = AsyncMock()
    monkeypatch.setattr("server.services.tmdb_service.asyncio.sleep", sleep)
    return TMDBService(config), client, sleep


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403, 400, 404, 422, 501])
async def test_permanent_errors_do_not_retry_or_become_empty_search(transport, status):
    service, client, sleep = transport
    client.get.return_value = httpx.Response(status, text="private-api-key")
    with pytest.raises(TMDBError) as caught:
        await service.search_series_by_api("Example")
    assert "private-api-key" not in str(caught.value)
    assert caught.value.status_code != 401
    if status == 401:
        assert isinstance(caught.value, TMDBInvalidCredentialsError)
    client.get.assert_awaited_once()
    sleep.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
async def test_transient_response_recovers_with_bounded_retry(transport, status):
    service, client, sleep = transport
    client.get.side_effect = [
        httpx.Response(status),
        httpx.Response(200, json={"results": [], "total_results": 0}),
    ]
    result = await service.search_series_by_api("Example")
    assert result.results == []
    assert client.get.await_count == 2
    sleep.assert_awaited_once_with(0.5)


@pytest.mark.asyncio
@pytest.mark.parametrize("retries, attempts", [(0, 1), (2, 3), (10, 4)])
async def test_retries_follow_configuration_and_hard_cap(transport, retries, attempts):
    service, client, sleep = transport
    service.config_service.get_system_config.return_value.retry_count = retries
    client.get.return_value = httpx.Response(503)
    with pytest.raises(TMDBError, match="503"):
        await service.search_series_by_api("Example")
    assert client.get.await_count == attempts
    assert sleep.await_count == attempts - 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure, expected", [
    (httpx.ReadTimeout("private-api-key"), TMDBTimeoutError),
    (httpx.ConnectError("private-api-key"), TMDBConnectionError),
])
async def test_network_errors_are_classified_without_leaking_credentials(transport, failure, expected):
    service, client, sleep = transport
    client.get.side_effect = failure
    with pytest.raises(expected) as caught:
        await service.search_series_by_api("Example")
    assert "private-api-key" not in str(caught.value)
    assert client.get.await_count == 3
    assert sleep.await_count == 2


@pytest.mark.asyncio
async def test_retry_after_seconds_are_respected(transport):
    service, client, sleep = transport
    client.get.side_effect = [
        httpx.Response(429, headers={"Retry-After": "2"}),
        httpx.Response(200, json={"results": []}),
    ]
    await service.search_series_by_api("Example")
    sleep.assert_awaited_once_with(2.0)


@pytest.mark.asyncio
@pytest.mark.parametrize("header", [
    "120",
    format_datetime(datetime.now(timezone.utc) + timedelta(hours=1), usegmt=True),
])
async def test_long_retry_after_does_not_sleep_or_retry_early(transport, header):
    service, client, sleep = transport
    client.get.return_value = httpx.Response(429, headers={"Retry-After": header})
    with pytest.raises(TMDBRateLimitError):
        await service.search_series_by_api("Example")
    client.get.assert_awaited_once()
    sleep.assert_not_awaited()


@pytest.mark.parametrize("header", ["garbage", "-5", "inf", ""])
def test_malformed_retry_after_is_ignored(header):
    assert TMDBService._retry_after(httpx.Response(429, headers={"Retry-After": header})) is None


@pytest.mark.asyncio
async def test_backoff_cannot_exceed_remaining_timeout(transport, monkeypatch):
    service, client, sleep = transport
    service.config_service.get_system_config.return_value.task_timeout = 1
    monkeypatch.setattr("server.services.tmdb_service.monotonic", lambda: 0.0)
    client.get.return_value = httpx.Response(429, headers={"Retry-After": "2"})
    with pytest.raises(TMDBRateLimitError):
        await service.search_series_by_api("Example")
    client.get.assert_awaited_once()
    sleep.assert_not_awaited()


@pytest.mark.asyncio
async def test_entire_request_has_a_deadline(transport):
    service, client, sleep = transport
    service.config_service.get_system_config.return_value.task_timeout = 0.01

    async def never_respond(*args, **kwargs):
        await asyncio.Event().wait()

    client.get.side_effect = never_respond
    with pytest.raises(TMDBTimeoutError):
        await service.search_series_by_api("Example")
    client.get.assert_awaited_once()
    sleep.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancellation_is_not_retried(transport):
    service, client, sleep = transport
    client.get.side_effect = asyncio.CancelledError
    with pytest.raises(asyncio.CancelledError):
        await service.search_series_by_api("Example")
    client.get.assert_awaited_once()
    sleep.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_token_never_contacts_upstream(transport):
    service, client, sleep = transport
    service.config_service.get_api_token.return_value = None
    with pytest.raises(TMDBNotConfiguredError):
        await service.search_series_by_api("Example")
    client.get.assert_not_awaited()
    sleep.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("bearer", [True, False])
async def test_request_authentication_preserves_caller_params(transport, bearer):
    service, client, _ = transport
    token = "eyJprivate" if bearer else "private-api-key"
    service.config_service.get_api_token.return_value = token
    client.get.return_value = httpx.Response(200)
    params = {"language": "ja-JP"}
    await service._make_api_request("/tv/1", params)
    assert params == {"language": "ja-JP"}
    kwargs = client.get.call_args.kwargs
    if bearer:
        assert kwargs["headers"]["Authorization"] == f"Bearer {token}"
        assert "api_key" not in kwargs["params"]
    else:
        assert kwargs["params"]["api_key"] == token


@pytest.mark.asyncio
@pytest.mark.parametrize("method, args", [
    ("get_series_by_api", (1,)), ("get_season_by_api", (1, 0)),
])
@pytest.mark.parametrize("status", [404, 401, 429, 503])
async def test_detail_404_is_missing_but_failures_raise(transport, method, args, status):
    service, client, _ = transport
    client.get.return_value = httpx.Response(status)
    if status == 404:
        assert await getattr(service, method)(*args) is None
        client.get.assert_awaited_once()
    else:
        with pytest.raises(TMDBError):
            await getattr(service, method)(*args)


@pytest.mark.asyncio
@pytest.mark.parametrize("method, args", [
    ("search_series_by_api", ("Example",)),
    ("get_series_by_api", (1,)),
    ("get_season_by_api", (1, 0)),
])
@pytest.mark.parametrize("payload", [None, {}, [], {"results": None}, {"results": [None]}])
async def test_malformed_payload_is_not_missing_metadata(transport, method, args, payload):
    service, client, _ = transport
    client.get.return_value = httpx.Response(200, json=payload)
    with pytest.raises(TMDBError, match="数据格式无效"):
        await getattr(service, method)(*args)
    client.get.assert_awaited_once()
