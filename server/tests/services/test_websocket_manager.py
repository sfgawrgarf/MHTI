"""Focused tests for bounded and serialized WebSocket delivery."""

import asyncio

import pytest

from server.services.websocket_manager import ConnectionManager


class FakeWebSocket:
    def __init__(self, *, block_sends: bool = False) -> None:
        self.sent: list[dict] = []
        self.closed = False
        self.active_writes = 0
        self.max_active_writes = 0
        self.active_sends = 0
        self.max_active_sends = 0
        self.block_sends = block_sends
        self.release = asyncio.Event()

    async def send_json(self, message: dict) -> None:
        self.active_writes += 1
        self.max_active_writes = max(self.max_active_writes, self.active_writes)
        self.active_sends += 1
        self.max_active_sends = max(self.max_active_sends, self.active_sends)
        try:
            if self.block_sends:
                await self.release.wait()
            else:
                await asyncio.sleep(0)
            self.sent.append(message)
        finally:
            self.active_sends -= 1
            self.active_writes -= 1

    async def close(self, **_kwargs) -> None:
        self.active_writes += 1
        self.max_active_writes = max(self.max_active_writes, self.active_writes)
        try:
            await asyncio.sleep(0)
            self.closed = True
        finally:
            self.active_writes -= 1


def test_unsubscribe_removes_empty_subscription() -> None:
    manager = ConnectionManager()
    websocket = FakeWebSocket()
    manager.connect("client", websocket, "session")

    assert manager.subscribe("client", ["job"]) == ["job"]
    manager.unsubscribe("client", ["job"])

    assert "job" not in manager.subscriptions


def test_subscription_count_is_bounded_per_client() -> None:
    manager = ConnectionManager(max_subscriptions_per_client=2)
    websocket = FakeWebSocket()
    manager.connect("client", websocket, "session")

    assert manager.subscribe("client", ["one", "two", "three"]) == ["one", "two"]
    assert set(manager.subscriptions) == {"one", "two"}


@pytest.mark.asyncio
async def test_sends_are_serialized_per_connection() -> None:
    manager = ConnectionManager(send_timeout=1)
    websocket = FakeWebSocket()
    manager.connect("client", websocket, "session")

    results = await asyncio.gather(
        manager.send_to_client("client", {"sequence": 1}),
        manager.send_to_client("client", {"sequence": 2}),
    )

    assert results == [True, True]
    assert websocket.max_active_sends == 1
    assert websocket.sent == [{"sequence": 1}, {"sequence": 2}]


@pytest.mark.asyncio
async def test_stalled_send_times_out_and_disconnects_client() -> None:
    manager = ConnectionManager(send_timeout=0.01)
    websocket = FakeWebSocket(block_sends=True)
    manager.connect("client", websocket, "session")
    manager.subscribe("client", ["job"])

    sent = await manager.send_to_client("client", {"type": "update"})

    assert sent is False
    assert websocket.closed is True
    assert "client" not in manager.active_connections
    assert "job" not in manager.subscriptions


@pytest.mark.asyncio
async def test_close_is_serialized_behind_an_active_send() -> None:
    manager = ConnectionManager(send_timeout=1)
    websocket = FakeWebSocket(block_sends=True)
    manager.connect("client", websocket, "session")

    send_task = asyncio.create_task(
        manager.send_to_client("client", {"type": "update"})
    )
    await asyncio.sleep(0)
    close_task = asyncio.create_task(
        manager.close_client("client", code=1001, reason="timeout")
    )
    await asyncio.sleep(0)

    assert websocket.closed is False
    websocket.release.set()

    assert await send_task is True
    assert await close_task is True
    assert websocket.max_active_writes == 1
    assert "client" not in manager.active_connections
