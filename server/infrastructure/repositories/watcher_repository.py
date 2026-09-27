"""watched_folders 表仓储。"""

from __future__ import annotations

import aiosqlite
from datetime import datetime

from server.infrastructure.repositories.base import BaseRepository
from server.models.watcher import WatchedFolder, WatchedFolderCreate, WatchedFolderUpdate

_WATCHED_FOLDERS_DDL = """
    CREATE TABLE IF NOT EXISTS watched_folders (
        id TEXT PRIMARY KEY,
        path TEXT NOT NULL UNIQUE,
        enabled INTEGER DEFAULT 1,
        mode TEXT DEFAULT 'realtime',
        scan_interval_seconds INTEGER DEFAULT 60,
        file_stable_seconds INTEGER DEFAULT 30,
        auto_scrape INTEGER DEFAULT 1,
        output_dir TEXT,
        provider TEXT DEFAULT 'local',
        file_id TEXT,
        last_scan TEXT,
        created_at TEXT NOT NULL
    )
"""


class WatcherRepository(BaseRepository):
    """监控目录表访问。"""

    async def _ensure_custom_schema(self, db: aiosqlite.Connection) -> None:
        await db.execute(_WATCHED_FOLDERS_DDL)

    async def ensure_schema(self) -> None:
        """兼容旧数据库：补齐缺失列（幂等）。"""
        async with self._connect() as db:
            cursor = await db.execute("PRAGMA table_info(watched_folders)")
            columns = [row[1] for row in await cursor.fetchall()]
            if "mode" not in columns:
                await db.execute("ALTER TABLE watched_folders ADD COLUMN mode TEXT DEFAULT 'realtime'")
            if "output_dir" not in columns:
                await db.execute("ALTER TABLE watched_folders ADD COLUMN output_dir TEXT")
            if "provider" not in columns:
                await db.execute("ALTER TABLE watched_folders ADD COLUMN provider TEXT DEFAULT 'local'")
            if "file_id" not in columns:
                await db.execute("ALTER TABLE watched_folders ADD COLUMN file_id TEXT")
            await db.commit()

    async def insert_folder(
        self, folder_id: str, data: WatchedFolderCreate, created_at: str
    ) -> None:
        async with self._connect() as db:
            await db.execute(
                """
                INSERT INTO watched_folders
                (id, path, enabled, mode, scan_interval_seconds, file_stable_seconds, auto_scrape, output_dir, provider, file_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    folder_id,
                    data.path,
                    1 if data.enabled else 0,
                    data.mode.value,
                    data.scan_interval_seconds,
                    data.file_stable_seconds,
                    1 if data.auto_scrape else 0,
                    data.output_dir,
                    data.provider,
                    data.file_id,
                    created_at,
                ),
            )
            await db.commit()

    async def list_folders(self) -> tuple[list[aiosqlite.Row], int]:
        async with self._connect() as db:
            cursor = await db.execute("SELECT COUNT(*) as count FROM watched_folders")
            row = await cursor.fetchone()
            total = row["count"] if row else 0

            cursor = await db.execute(
                "SELECT * FROM watched_folders ORDER BY created_at DESC"
            )
            rows = await cursor.fetchall()

        return list(rows), total

    async def get_folder(self, folder_id: str) -> aiosqlite.Row | None:
        return await self._fetch_one(
            "SELECT * FROM watched_folders WHERE id = ?", (folder_id,)
        )

    async def update_folder(self, folder_id: str, update: WatchedFolderUpdate) -> None:
        updates = []
        values = []

        if update.path is not None:
            updates.append("path = ?")
            values.append(update.path)
        if update.enabled is not None:
            updates.append("enabled = ?")
            values.append(1 if update.enabled else 0)
        if update.mode is not None:
            updates.append("mode = ?")
            values.append(update.mode.value)
        if update.scan_interval_seconds is not None:
            updates.append("scan_interval_seconds = ?")
            values.append(update.scan_interval_seconds)
        if update.file_stable_seconds is not None:
            updates.append("file_stable_seconds = ?")
            values.append(update.file_stable_seconds)
        if update.auto_scrape is not None:
            updates.append("auto_scrape = ?")
            values.append(1 if update.auto_scrape else 0)
        if update.output_dir is not None:
            updates.append("output_dir = ?")
            values.append(update.output_dir)
        if update.provider is not None:
            updates.append("provider = ?")
            values.append(update.provider)
        if update.file_id is not None:
            updates.append("file_id = ?")
            values.append(update.file_id)

        if not updates:
            return

        values.append(folder_id)
        async with self._connect() as db:
            await db.execute(
                f"UPDATE watched_folders SET {', '.join(updates)} WHERE id = ?",
                values,
            )
            await db.commit()

    async def delete_folder(self, folder_id: str) -> bool:
        return await self._execute(
            "DELETE FROM watched_folders WHERE id = ?", (folder_id,)
        ) > 0

    async def replace_folders(self, folders: list[WatchedFolder]) -> None:
        """Replace the watched-folder snapshot in one database transaction."""
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            await db.execute("DELETE FROM watched_folders")
            for folder in folders:
                await db.execute(
                    """
                    INSERT INTO watched_folders
                    (id, path, enabled, mode, scan_interval_seconds,
                     file_stable_seconds, auto_scrape, output_dir, provider,
                     file_id, last_scan, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        folder.id,
                        folder.path,
                        1 if folder.enabled else 0,
                        folder.mode.value,
                        folder.scan_interval_seconds,
                        folder.file_stable_seconds,
                        1 if folder.auto_scrape else 0,
                        folder.output_dir,
                        folder.provider,
                        folder.file_id,
                        folder.last_scan.isoformat() if folder.last_scan else None,
                        (folder.created_at or datetime.now()).isoformat(),
                    ),
                )
            await db.commit()

    async def update_last_scan(self, folder_id: str, scanned_at: str) -> None:
        await self._execute(
            "UPDATE watched_folders SET last_scan = ? WHERE id = ?",
            (scanned_at, folder_id),
        )
