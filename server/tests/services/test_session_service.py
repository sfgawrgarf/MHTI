"""Session history field-mapping regressions."""

from contextlib import asynccontextmanager

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
