"""scraped_files 表仓储。"""

from __future__ import annotations

import aiosqlite

from server.infrastructure.repositories.base import BaseRepository


class ScrapedFileRepository(BaseRepository):
    """已刮削文件记录表访问。"""

    async def upsert_record(
        self,
        *,
        record_id: str,
        source_path: str,
        target_path: str | None,
        file_size: int,
        tmdb_id: int | None,
        season: int | None,
        episode: int | None,
        title: str | None,
        scraped_at: str,
        history_record_id: str | None,
    ) -> None:
        async with self._connect() as db:
            # 使用 INSERT OR REPLACE 实现 upsert
            await db.execute(
                """
                INSERT OR REPLACE INTO scraped_files
                (id, source_path, target_path, file_size, tmdb_id, season, episode, title, scraped_at, history_record_id)
                VALUES (
                    COALESCE((SELECT id FROM scraped_files WHERE source_path = ?), ?),
                    ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    source_path, record_id,
                    source_path, target_path, file_size,
                    tmdb_id, season, episode, title,
                    scraped_at, history_record_id,
                ),
            )
            await db.commit()

    async def update_by_history_record(
        self,
        *,
        history_record_id: str,
        source_path: str | None,
        target_path: str | None,
        file_size: int,
        tmdb_id: int | None,
        season: int | None,
        episode: int | None,
        title: str | None,
        scraped_at: str,
    ) -> int:
        """把某条历史记录的登记行就地更新（重刮用），返回更新行数。

        重刮会换产物路径（甚至换源文件），若按 source_path upsert 就会多出一条登记行，
        「已刮削文件」页与删除弹窗都跟着出现重复项。这里按历史记录定位原行，id 保持不变。
        """
        async with self._connect() as db:
            cursor = await db.execute(
                """
                UPDATE scraped_files SET
                    source_path = COALESCE(NULLIF(?, ''), source_path),
                    target_path = ?, file_size = ?, tmdb_id = ?,
                    season = ?, episode = ?, title = ?, scraped_at = ?
                WHERE id = (
                    SELECT id FROM scraped_files
                    WHERE history_record_id = ?
                    ORDER BY scraped_at DESC LIMIT 1
                )
                """,
                (
                    source_path or "", target_path, file_size, tmdb_id,
                    season, episode, title, scraped_at,
                    history_record_id,
                ),
            )
            await db.commit()
            return cursor.rowcount or 0

    async def restore_row(self, row: dict) -> None:
        """按快照原样插回一行（撤销记录删除时恢复其文件登记）。"""
        if not row:
            return
        columns = list(row.keys())
        placeholders = ",".join("?" * len(columns))
        async with self._connect() as db:
            await db.execute(
                f"INSERT OR REPLACE INTO scraped_files ({','.join(columns)}) VALUES ({placeholders})",
                tuple(row[c] for c in columns),
            )
            await db.commit()

    async def list_by_history_record(self, history_record_id: str) -> list[aiosqlite.Row]:
        """按历史记录 ID 列出登记过的文件（源文件 / 整理后文件路径的唯一来源）。"""
        return await self._fetch_all(
            "SELECT * FROM scraped_files WHERE history_record_id = ? ORDER BY scraped_at DESC",
            (history_record_id,),
        )

    async def get_by_source_path(self, source_path: str) -> aiosqlite.Row | None:
        """Return the registration for one source path, if present."""
        return await self._fetch_one(
            "SELECT * FROM scraped_files WHERE source_path = ?",
            (source_path,),
        )

    async def list_records(
        self,
        *,
        limit: int,
        offset: int,
        search: str | None,
    ) -> tuple[list[aiosqlite.Row], int]:
        """List registrations with optional path/title search."""
        conditions: list[str] = []
        params: list[object] = []
        if search:
            conditions.append("(source_path LIKE ? OR target_path LIKE ? OR title LIKE ?)")
            pattern = f"%{search}%"
            params.extend((pattern, pattern, pattern))
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        async with self._connect() as db:
            cursor = await db.execute(
                f"SELECT COUNT(*) AS count FROM scraped_files {where}", params
            )
            row = await cursor.fetchone()
            total = int(row["count"]) if row else 0
            cursor = await db.execute(
                f"""
                SELECT * FROM scraped_files {where}
                ORDER BY scraped_at DESC
                LIMIT ? OFFSET ?
                """,
                params + [limit, offset],
            )
            return list(await cursor.fetchall()), total

    async def delete_records(self, ids: list[str]) -> int:
        """Delete registrations by their IDs."""
        if not ids:
            return 0
        placeholders = ",".join("?" * len(ids))
        return await self._execute(
            f"DELETE FROM scraped_files WHERE id IN ({placeholders})",
            tuple(ids),
        )

    async def clear_all(self) -> int:
        """Delete all registrations."""
        return await self._execute("DELETE FROM scraped_files")

    async def delete_by_any_paths(self, paths: list[str]) -> int:
        """按源路径或产物路径删除登记行。

        删掉产物时传进来的是 target_path，只用 source_path 匹配会留下失效登记，
        导致同一个源文件再扫到时被判为已处理而跳过。
        """
        if not paths:
            return 0

        placeholders = ",".join("?" * len(paths))
        return await self._execute(
            f"DELETE FROM scraped_files WHERE source_path IN ({placeholders})"
            f" OR target_path IN ({placeholders})",
            tuple(paths) + tuple(paths),
        )
