"""config 表仓储 - 全局 KV 配置。"""

from __future__ import annotations

import aiosqlite

from server.infrastructure.repositories.base import BaseRepository

_CONFIG_TABLE_DDL = """
    CREATE TABLE IF NOT EXISTS config (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        key TEXT UNIQUE NOT NULL,
        value TEXT NOT NULL,
        encrypted INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
"""


class ConfigRepository(BaseRepository):
    """config 表访问。"""

    async def _ensure_custom_schema(self, db: aiosqlite.Connection) -> None:
        await db.execute(_CONFIG_TABLE_DDL)

    async def get_value(self, key: str) -> tuple[str, bool] | None:
        """读取配置值，返回 (value, encrypted)；不存在返回 None。"""
        row = await self._fetch_one(
            "SELECT value, encrypted FROM config WHERE key = ?", (key,)
        )
        if row is None:
            return None
        return row["value"], bool(row["encrypted"])

    async def set_value(
        self, key: str, value: str, encrypted: bool, updated_at: str
    ) -> None:
        async with self._connect() as db:
            await db.execute(
                """
                INSERT INTO config (key, value, encrypted, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    encrypted = excluded.encrypted,
                    updated_at = excluded.updated_at
                """,
                (key, value, 1 if encrypted else 0, updated_at),
            )
            await db.commit()

    async def delete_value(self, key: str) -> bool:
        return await self._execute("DELETE FROM config WHERE key = ?", (key,)) > 0

    async def delete_by_prefix(self, prefix: str) -> int:
        return await self._execute(
            "DELETE FROM config WHERE substr(key, 1, ?) = ?",
            (len(prefix), prefix),
        )

    async def list_by_prefix(self, prefix: str) -> list[tuple[str, str, bool]]:
        """List config rows under a namespace for TTL/maintenance tasks."""
        rows = await self._fetch_all(
            "SELECT key, value, encrypted FROM config "
            "WHERE substr(key, 1, ?) = ? ORDER BY key",
            (len(prefix), prefix),
        )
        return [(row["key"], row["value"], bool(row["encrypted"])) for row in rows]

    async def exists(self, key: str) -> bool:
        row = await self._fetch_one("SELECT 1 FROM config WHERE key = ?", (key,))
        return row is not None
