"""sessions 与 login_history 表仓储。"""

from __future__ import annotations

import aiosqlite

from server.infrastructure.repositories.base import BaseRepository


class SessionRepository(BaseRepository):
    """会话与登录历史表访问。"""

    # ---- sessions ----

    async def insert_session(
        self,
        *,
        session_id: str,
        user_id: int,
        refresh_token_hash: str,
        device_name: str | None,
        device_type: str,
        ip_address: str | None,
        user_agent: str | None,
        expires_at: str,
    ) -> None:
        async with self._connect() as db:
            await db.execute(
                """
                INSERT INTO sessions
                (id, user_id, refresh_token_hash, device_name, device_type,
                 ip_address, user_agent, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    user_id,
                    refresh_token_hash,
                    device_name,
                    device_type,
                    ip_address,
                    user_agent,
                    expires_at,
                ),
            )
            await db.commit()

    async def count_sessions(self, user_id: int) -> int:
        row = await self._fetch_one(
            "SELECT COUNT(*) FROM sessions WHERE user_id = ?", (user_id,)
        )
        return row[0] if row else 0

    async def delete_oldest_sessions(self, user_id: int, limit: int) -> None:
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            cursor = await db.execute(
                """
                SELECT id FROM sessions
                WHERE user_id = ?
                ORDER BY last_used_at ASC
                LIMIT ?
                """,
                (user_id, limit),
            )
            session_ids = [str(row[0]) for row in await cursor.fetchall()]
            if session_ids:
                placeholders = ",".join("?" * len(session_ids))
                await db.execute(
                    f"DELETE FROM refresh_token_history WHERE session_id IN ({placeholders})",
                    session_ids,
                )
                await db.execute(
                    f"DELETE FROM sessions WHERE id IN ({placeholders})",
                    session_ids,
                )
            await db.commit()

    async def rotate_refresh_token(
        self,
        current_hash: str,
        new_hash: str,
        now: str,
    ) -> tuple[str | None, int | None, bool]:
        """Atomically rotate a refresh token and detect token replay.

        Returns ``(session_id, user_id, reused)``.  A successful rotation has
        ``reused=False``.  When an already-used token is submitted again, the
        matching session is revoked and ``reused=True`` is returned.
        """
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            cursor = await db.execute(
                """
                SELECT id, user_id FROM sessions
                WHERE refresh_token_hash = ? AND expires_at > ?
                """,
                (current_hash, now),
            )
            row = await cursor.fetchone()
            if row:
                cursor = await db.execute(
                    """
                    UPDATE sessions
                    SET refresh_token_hash = ?, last_used_at = ?
                    WHERE id = ? AND refresh_token_hash = ? AND expires_at > ?
                    """,
                    (new_hash, now, row[0], current_hash, now),
                )
                if cursor.rowcount != 1:
                    await db.rollback()
                    return None, None, False
                await db.execute(
                    """
                    INSERT OR IGNORE INTO refresh_token_history
                    (token_hash, session_id, used_at)
                    VALUES (?, ?, ?)
                    """,
                    (current_hash, row[0], now),
                )
                await db.commit()
                return str(row[0]), int(row[1]), False

            cursor = await db.execute(
                "SELECT session_id FROM refresh_token_history WHERE token_hash = ?",
                (current_hash,),
            )
            history_row = await cursor.fetchone()
            if history_row:
                session_id = str(history_row[0])
                await db.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
                await db.execute(
                    "DELETE FROM refresh_token_history WHERE session_id = ?",
                    (session_id,),
                )
                await db.commit()
                return session_id, None, True

            await db.commit()
            return None, None, False

    async def delete_session(self, session_id: str) -> bool:
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            await db.execute(
                "DELETE FROM refresh_token_history WHERE session_id = ?",
                (session_id,),
            )
            cursor = await db.execute(
                "DELETE FROM sessions WHERE id = ?", (session_id,)
            )
            await db.commit()
            return cursor.rowcount > 0

    async def delete_user_sessions(
        self, user_id: int, except_session_id: str | None = None
    ) -> int:
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            if except_session_id:
                where_sql = "user_id = ? AND id != ?"
                params = (user_id, except_session_id)
            else:
                where_sql = "user_id = ?"
                params = (user_id,)
            await db.execute(
                f"DELETE FROM refresh_token_history WHERE session_id IN "
                f"(SELECT id FROM sessions WHERE {where_sql})",
                params,
            )
            cursor = await db.execute(
                f"DELETE FROM sessions WHERE {where_sql}", params
            )
            await db.commit()
            return cursor.rowcount

    async def delete_expired(self, now: str) -> None:
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            await db.execute(
                "DELETE FROM refresh_token_history WHERE session_id IN "
                "(SELECT id FROM sessions WHERE expires_at <= ?)",
                (now,),
            )
            await db.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
            await db.execute(
                "DELETE FROM refresh_token_history WHERE session_id NOT IN "
                "(SELECT id FROM sessions)"
            )
            await db.commit()

    async def list_user_sessions(self, user_id: int) -> list[aiosqlite.Row]:
        return await self._fetch_all(
            """
            SELECT id, device_name, device_type, ip_address,
                   created_at, last_used_at, expires_at, user_agent
            FROM sessions
            WHERE user_id = ?
            ORDER BY last_used_at DESC
            """,
            (user_id,),
        )

    # ---- login_history ----

    async def insert_login_history(
        self,
        *,
        username: str,
        ip_address: str | None,
        user_agent: str | None,
        device_name: str | None,
        success: int,
        failure_reason: str | None,
        session_id: str | None,
    ) -> None:
        async with self._connect() as db:
            await db.execute(
                """
                INSERT INTO login_history
                (username, ip_address, user_agent, device_name, success, failure_reason, session_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    username,
                    ip_address,
                    user_agent,
                    device_name,
                    success,
                    failure_reason,
                    session_id,
                ),
            )
            await db.commit()

    async def count_login_history(self, username: str) -> int:
        row = await self._fetch_one(
            "SELECT COUNT(*) FROM login_history WHERE username = ?", (username,)
        )
        return row[0] if row else 0

    async def list_login_history(
        self, username: str, limit: int, offset: int
    ) -> list[aiosqlite.Row]:
        return await self._fetch_all(
            """
            SELECT id, ip_address, user_agent, device_name, login_time, success, failure_reason
            FROM login_history
            WHERE username = ?
            ORDER BY login_time DESC
            LIMIT ? OFFSET ?
            """,
            (username, limit, offset),
        )

    async def delete_old_history(self, cutoff: str) -> int:
        return await self._execute(
            "DELETE FROM login_history WHERE login_time < ?",
            (cutoff,),
        )
