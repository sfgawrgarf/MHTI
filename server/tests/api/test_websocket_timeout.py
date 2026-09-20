"""Regression tests for serialized WebSocket timeout handling."""

from unittest.mock import AsyncMock, Mock

import pytest

from server.api.websocket import _timeout_monitor


@pytest.mark.asyncio
async def test_timeout_monitor_closes_through_connection_manager(monkeypatch):
    manager = Mock()
    manager.close_client = AsyncMock(return_value=True)
    monkeypatch.setattr("server.api.websocket.asyncio.sleep", AsyncMock())

    await _timeout_monitor(
        "client",
        manager,
        lambda: -1_000_000.0,
    )

    manager.close_client.assert_awaited_once_with(
        "client",
        code=1001,
        reason="Client heartbeat timeout",
    )


@pytest.mark.asyncio
async def test_timeout_monitor_revalidates_and_closes_revoked_session(monkeypatch):
    manager = Mock()
    manager.close_client = AsyncMock(return_value=True)
    monkeypatch.setattr("server.api.websocket.asyncio.sleep", AsyncMock())
    monkeypatch.setattr(
        "server.services.session_service.session_service.is_session_active",
        AsyncMock(return_value=False),
    )

    await _timeout_monitor(
        "client",
        manager,
        lambda: 0.0,
        session_id="revoked-session",
        username="admin",
    )

    manager.close_client.assert_awaited_once_with(
        "client",
        code=4401,
        reason="Session revoked",
    )


@pytest.mark.asyncio
async def test_timeout_monitor_fails_closed_when_session_validation_errors(monkeypatch):
    manager = Mock()
    manager.close_client = AsyncMock(return_value=True)
    monkeypatch.setattr("server.api.websocket.asyncio.sleep", AsyncMock())
    monkeypatch.setattr(
        "server.services.session_service.session_service.is_session_active",
        AsyncMock(side_effect=RuntimeError("database unavailable")),
    )

    await _timeout_monitor(
        "client",
        manager,
        lambda: 0.0,
        session_id="session",
        username="admin",
    )

    manager.close_client.assert_awaited_once_with(
        "client",
        code=1011,
        reason="Session validation failed",
    )
