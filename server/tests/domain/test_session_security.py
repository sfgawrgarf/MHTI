"""刷新令牌轮换与重放撤销的回归测试。"""

import aiosqlite
import pytest

from server.infrastructure.db import configure_connection, create_all_tables
from server.infrastructure.repositories.session_repository import SessionRepository


@pytest.mark.asyncio
async def test_refresh_token_replay_revokes_session(temp_db):
    """旧 refresh token 只能使用一次，重放会撤销整个会话。"""
    async with aiosqlite.connect(temp_db) as db:
        await configure_connection(db)
        await create_all_tables(db)
        await db.execute(
            """
            INSERT INTO sessions
            (id, user_id, refresh_token_hash, expires_at)
            VALUES (?, ?, ?, ?)
            """,
            ("session-1", 1, "old-hash", "2099-01-01T00:00:00+00:00"),
        )
        await db.commit()

    repository = SessionRepository(db_path=temp_db)
    session_id, user_id, reused = await repository.rotate_refresh_token(
        "old-hash", "new-hash", "2026-09-29T00:00:00+00:00"
    )

    assert (session_id, user_id, reused) == ("session-1", 1, False)

    session_id, user_id, reused = await repository.rotate_refresh_token(
        "old-hash", "attacker-hash", "2026-09-29T00:01:00+00:00"
    )

    assert (session_id, user_id, reused) == ("session-1", None, True)
    assert await repository._fetch_one(
        "SELECT id FROM sessions WHERE id = ?", ("session-1",)
    ) is None
