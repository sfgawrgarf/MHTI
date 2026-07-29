"""Regression tests for SQLite connection setup."""

import asyncio

import pytest

from server.core.db.connection import configure_connection


class _RecordingConnection:
    def __init__(self) -> None:
        self.statements: list[str] = []

    async def execute(self, statement: str) -> None:
        self.statements.append(statement)


@pytest.mark.asyncio
async def test_busy_timeout_is_configured_before_wal_mode() -> None:
    """Concurrent first-use connections need a busy handler before WAL."""
    db = _RecordingConnection()

    await configure_connection(db)  # type: ignore[arg-type]

    assert db.statements[:2] == [
        "PRAGMA busy_timeout=30000",
        "PRAGMA journal_mode=WAL",
    ]


@pytest.mark.asyncio
async def test_wal_mode_changes_are_serialized_within_event_loop() -> None:
    """Only one same-process connection may request WAL mode at a time."""
    active_wal_changes = 0
    max_active_wal_changes = 0

    class ContendedConnection(_RecordingConnection):
        async def execute(self, statement: str) -> None:
            nonlocal active_wal_changes, max_active_wal_changes
            if statement == "PRAGMA journal_mode=WAL":
                active_wal_changes += 1
                max_active_wal_changes = max(
                    max_active_wal_changes,
                    active_wal_changes,
                )
                await asyncio.sleep(0)
                active_wal_changes -= 1
            await super().execute(statement)

    connections = [ContendedConnection() for _ in range(8)]
    await asyncio.gather(
        *(configure_connection(db) for db in connections),  # type: ignore[arg-type]
    )

    assert max_active_wal_changes == 1
