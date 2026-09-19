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
