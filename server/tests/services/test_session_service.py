"""Session history field-mapping regressions."""

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, Mock

import aiosqlite
import pytest

from server.core.db import configure_connection, create_all_tables
from server.services import session_service as session_module
from server.services.session_service import SessionService


@pytest.mark.asyncio
async def test_login_history_preserves_device_name_and_user_agent(temp_db, monkeypatch):
    async with aiosqlite.connect(temp_db) as db:
        await configure_connection(db)
        await create_all_tables(db)
        await db.commit()

    @asynccontextmanager
    async def isolated_db_context():
        async with aiosqlite.connect(temp_db) as db:
            await configure_connection(db)
            yield db

    monkeypatch.setattr(session_module, "db_context", isolated_db_context)
    service = SessionService()
    await service.record_login(
        username="admin",
        success=True,
        ip_address="192.0.2.10",
        user_agent="ExampleBrowser/1.0",
        device_name="Living Room PC",
    )

    records, total = await service.get_login_history("admin")

    assert total == 1
    assert records[0].device_name == "Living Room PC"
    assert records[0].user_agent == "ExampleBrowser/1.0"


@pytest.mark.asyncio
async def test_revoking_sessions_immediately_closes_their_websockets(
    temp_db, monkeypatch
):
    async with aiosqlite.connect(temp_db) as db:
        await configure_connection(db)
        await create_all_tables(db)
        cursor = await db.execute(
            "INSERT INTO admin (username, password_hash) VALUES ('admin', 'hash')"
        )
        user_id = cursor.lastrowid
        expires = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        await db.executemany(
            """INSERT INTO sessions
               (id, user_id, refresh_token_hash, expires_at)
               VALUES (?, ?, ?, ?)""",
            [
                ("current", user_id, "current-hash", expires),
                ("revoked", user_id, "revoked-hash", expires),
            ],
        )
        await db.commit()

    @asynccontextmanager
    async def isolated_db_context():
        async with aiosqlite.connect(temp_db) as db:
            await configure_connection(db)
            yield db

    manager = Mock()
    manager.close_session = AsyncMock(return_value=1)
    monkeypatch.setattr(session_module, "db_context", isolated_db_context)
    monkeypatch.setattr(
        "server.services.websocket_manager.get_ws_manager", lambda: manager
    )

    count = await SessionService().revoke_all_sessions(
        user_id, except_session_id="current"
    )

    assert count == 1
    manager.close_session.assert_awaited_once_with("revoked")
    async with aiosqlite.connect(temp_db) as db:
        cursor = await db.execute("SELECT id FROM sessions ORDER BY id")
        assert await cursor.fetchall() == [("current",)]
