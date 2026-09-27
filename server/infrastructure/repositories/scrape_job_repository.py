"""scrape_jobs 表仓储。"""

from __future__ import annotations

from datetime import datetime

import aiosqlite

from server.infrastructure.repositories.base import BaseRepository
from server.models.scrape_job import ScrapeJobCreate, ScrapeJobSource, ScrapeJobStatus

_SCRAPE_JOBS_DDL = """
    CREATE TABLE IF NOT EXISTS scrape_jobs (
        id TEXT PRIMARY KEY,
        file_path TEXT NOT NULL,
        output_dir TEXT NOT NULL,
        metadata_dir TEXT,
        link_mode TEXT,
        source TEXT NOT NULL DEFAULT 'manual',
        source_id INTEGER,
        advanced_settings TEXT,
        file_locator TEXT,
        output_locator TEXT,
        metadata_locator TEXT,
        allow_local_output INTEGER DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'pending',
        created_at TEXT NOT NULL,
        started_at TEXT,
        finished_at TEXT,
        error_message TEXT,
        history_record_id TEXT,
        replaces_job_id TEXT,
        replaced_by_job_id TEXT,
        correction_history_id TEXT,
        correction_tmdb_id INTEGER,
        correction_season INTEGER,
        correction_episode INTEGER,
        continuation_history_id TEXT,
        file_action TEXT,
        selection_log TEXT,
        skip_emby_check INTEGER DEFAULT 0
    )
"""


class ScrapeJobRepository(BaseRepository):
    """刮削任务表访问。"""

    async def _ensure_custom_schema(self, db: aiosqlite.Connection) -> None:
        await db.execute(_SCRAPE_JOBS_DDL)

    async def ensure_schema(self) -> None:
        """兼容旧数据库：补齐缺失列（幂等）。"""
        async with self._connect() as db:
            async with db.execute("PRAGMA table_info(scrape_jobs)") as cursor:
                columns = {row[1] for row in await cursor.fetchall()}
            if not columns:
                raise aiosqlite.OperationalError("Missing scrape_jobs table")
            missing = {
                "link_mode": "TEXT",
                "advanced_settings": "TEXT",
                "file_locator": "TEXT",
                "output_locator": "TEXT",
                "metadata_locator": "TEXT",
                "allow_local_output": "INTEGER DEFAULT 0",
                "replaces_job_id": "TEXT",
                "replaced_by_job_id": "TEXT",
                "correction_history_id": "TEXT",
                "correction_tmdb_id": "INTEGER",
                "correction_season": "INTEGER",
                "correction_episode": "INTEGER",
                "continuation_history_id": "TEXT",
                "file_action": "TEXT",
                "selection_log": "TEXT",
                "skip_emby_check": "INTEGER DEFAULT 0",
            }
            for name, column_type in missing.items():
                if name not in columns:
                    await db.execute(
                        f"ALTER TABLE scrape_jobs ADD COLUMN {name} {column_type}"
                    )
            await db.commit()

    async def get_pending_by_path(self, file_path: str) -> aiosqlite.Row | None:
        """获取指定路径仍在处理中的任务（pending/running/pending_action）。"""
        return await self._fetch_one(
            """SELECT * FROM scrape_jobs
            WHERE file_path = ? AND status IN ('pending', 'running', 'pending_action')
            ORDER BY created_at DESC LIMIT 1""",
            (file_path,),
        )

    async def get_pending_file_paths(self) -> set[str]:
        rows = await self._fetch_all(
            "SELECT file_path FROM scrape_jobs WHERE status IN ('pending', 'running', 'pending_action')"
        )
        return {row[0] for row in rows}

    async def has_media_version(self, source_fingerprint: str) -> bool:
        """Return whether a local source was already recorded as a version."""
        try:
            row = await self._fetch_one(
                "SELECT 1 FROM media_versions WHERE source_fingerprint = ? LIMIT 1",
                (source_fingerprint,),
            )
        except aiosqlite.OperationalError:
            # Standalone/custom databases may not carry the optional identity
            # tables yet; that is equivalent to having no recorded version.
            return False
        return row is not None

    async def has_skipped_history(
        self, file_path: str, file_fingerprint: str | None
    ) -> bool:
        """Return whether a watcher source was explicitly skipped by a user."""
        try:
            row = await self._fetch_one(
                """
                SELECT 1 FROM history_records
                WHERE status IN ('skipped', 'deleted')
                  AND (
                      (? IS NOT NULL AND file_fingerprint = ?)
                      OR folder_path = ?
                      OR instr(folder_path, ? || ' => ') = 1
                  )
                LIMIT 1
                """,
                (file_fingerprint, file_fingerprint, file_path, file_path),
            )
        except aiosqlite.OperationalError:
            return False
        return row is not None

    async def insert_job(
        self,
        job_id: str,
        data: ScrapeJobCreate,
        *,
        created_at: str,
        advanced_settings_json: str | None,
        file_locator_json: str | None,
        output_locator_json: str | None,
        metadata_locator_json: str | None,
        check_duplicate: bool = False,
    ) -> bool:
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            if data.continuation_history_id:
                cursor = await db.execute(
                    "SELECT status FROM history_records WHERE id = ?",
                    (data.continuation_history_id,),
                )
                history = await cursor.fetchone()
                if not history or history[0] not in {
                    "pending_action",
                    "failed",
                    "timeout",
                    "cancelled",
                    "skipped",
                }:
                    await db.rollback()
                    return False

            if check_duplicate:
                cursor = await db.execute(
                    """
                    SELECT 1 FROM scrape_jobs
                    WHERE file_path = ?
                      AND status IN ('pending', 'running', 'pending_action')
                    LIMIT 1
                    """,
                    (data.file_path,),
                )
                if await cursor.fetchone():
                    await db.rollback()
                    return False
            await db.execute(
                """
                INSERT INTO scrape_jobs
                (id, file_path, output_dir, metadata_dir, link_mode, source, source_id,
                 advanced_settings, file_locator, output_locator, metadata_locator,
                 allow_local_output, status, created_at, replaces_job_id,
                 correction_history_id, correction_tmdb_id, correction_season,
                 correction_episode, continuation_history_id, file_action,
                 selection_log, skip_emby_check)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    data.file_path,
                    data.output_dir,
                    data.metadata_dir,
                    data.link_mode.value if data.link_mode else None,
                    data.source.value,
                    data.source_id,
                    advanced_settings_json,
                    file_locator_json,
                    output_locator_json,
                    metadata_locator_json,
                    1 if data.allow_local_output else 0,
                    ScrapeJobStatus.PENDING.value,
                    created_at,
                    data.replaces_job_id,
                    data.correction_history_id,
                    data.correction_tmdb_id,
                    data.correction_season,
                    data.correction_episode,
                    data.continuation_history_id,
                    data.file_action,
                    data.selection_log,
                    1 if data.skip_emby_check else 0,
                ),
            )
            if data.replaces_job_id:
                cursor = await db.execute(
                    """
                    UPDATE scrape_jobs
                    SET status = 'replaced', finished_at = ?, replaced_by_job_id = ?
                    WHERE id = ? AND replaced_by_job_id IS NULL
                    """,
                    (created_at, job_id, data.replaces_job_id),
                )
                if cursor.rowcount != 1:
                    await db.rollback()
                    return False

            if data.continuation_history_id:
                cursor = await db.execute(
                    """
                    UPDATE history_records
                    SET status = 'running', error_message = '已排队，等待处理'
                    WHERE id = ? AND status IN (
                        'pending_action', 'failed', 'timeout', 'cancelled', 'skipped'
                    )
                    """,
                    (data.continuation_history_id,),
                )
                if cursor.rowcount != 1:
                    await db.rollback()
                    return False
            await db.commit()
            return True

    async def list_jobs(
        self,
        limit: int = 50,
        offset: int = 0,
        source: ScrapeJobSource | None = None,
        source_id: int | None = None,
        status: ScrapeJobStatus | None = None,
    ) -> tuple[list[aiosqlite.Row], int]:
        conditions = []
        params = []

        if source:
            conditions.append("source = ?")
            params.append(source.value)
        if source_id is not None:
            conditions.append("source_id = ?")
            params.append(source_id)
        if status:
            conditions.append("status = ?")
            params.append(status.value)

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        async with self._connect() as db:
            cursor = await db.execute(
                f"SELECT COUNT(*) as count FROM scrape_jobs {where_clause}",
                params,
            )
            row = await cursor.fetchone()
            total = row["count"] if row else 0

            cursor = await db.execute(
                f"""
                SELECT * FROM scrape_jobs {where_clause}
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?
                """,
                params + [limit, offset],
            )
            rows = await cursor.fetchall()

        return list(rows), total

    async def get_job_raw(self, job_id: str) -> aiosqlite.Row | None:
        return await self._fetch_one(
            "SELECT * FROM scrape_jobs WHERE id = ?", (job_id,)
        )

    async def prepare_recovery(self) -> list[str]:
        """Reset interrupted jobs and return persisted pending IDs.

        A worker can be interrupted between claiming a row and writing its
        terminal state.  Recovery must make that row runnable again before the
        in-memory queue is rebuilt.  The linked history is retained as a
        failure marker so the next run starts with an auditable boundary.
        """
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            await db.execute(
                """
                UPDATE history_records
                SET status = 'failed',
                    error_message = COALESCE(
                        error_message,
                        '任务因服务重启中断，已重新排队'
                    )
                WHERE id IN (
                    SELECT history_record_id FROM scrape_jobs
                    WHERE status = 'running' AND history_record_id IS NOT NULL
                )
                  AND status = 'running'
                """
            )
            await db.execute(
                """
                UPDATE scrape_jobs
                SET status = 'pending', started_at = NULL, finished_at = NULL,
                    error_message = NULL, history_record_id = NULL
                WHERE status = 'running'
                """
            )
            cursor = await db.execute(
                """
                SELECT id FROM scrape_jobs
                WHERE status = 'pending'
                ORDER BY created_at ASC
                """
            )
            rows = await cursor.fetchall()
            await db.commit()
        return [str(row[0]) for row in rows]

    async def claim_job(self, job_id: str) -> bool:
        """Atomically claim a pending job for one worker."""
        async with self._connect() as db:
            cursor = await db.execute(
                """
                UPDATE scrape_jobs
                SET status = ?, started_at = ?
                WHERE id = ? AND status = ?
                """,
                (
                    ScrapeJobStatus.RUNNING.value,
                    datetime.now().isoformat(),
                    job_id,
                    ScrapeJobStatus.PENDING.value,
                ),
            )
            await db.commit()
            return cursor.rowcount == 1

    async def update_job(
        self,
        job_id: str,
        status: ScrapeJobStatus | None = None,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
        error_message: str | None = None,
        clear_error_message: bool = False,
        history_record_id: str | None = None,
        replaced_by_job_id: str | None = None,
        expected_status: ScrapeJobStatus | None = None,
    ) -> bool:
        updates = []
        params = []

        if status is not None:
            updates.append("status = ?")
            params.append(status.value)
        if started_at is not None:
            updates.append("started_at = ?")
            params.append(started_at.isoformat())
        if finished_at is not None:
            updates.append("finished_at = ?")
            params.append(finished_at.isoformat())
        if clear_error_message:
            updates.append("error_message = NULL")
        elif error_message is not None:
            updates.append("error_message = ?")
            params.append(error_message)
        if history_record_id is not None:
            updates.append("history_record_id = ?")
            params.append(history_record_id)
        if replaced_by_job_id is not None:
            updates.append("replaced_by_job_id = ?")
            params.append(replaced_by_job_id)

        if not updates:
            return False

        params.append(job_id)
        where_clause = "id = ?"
        if expected_status is not None:
            where_clause += " AND status = ?"
            params.append(expected_status.value)

        async with self._connect() as db:
            cursor = await db.execute(
                f"UPDATE scrape_jobs SET {', '.join(updates)} WHERE {where_clause}",
                params,
            )
            await db.commit()
            return cursor.rowcount == 1

    async def reset_job_to_pending(self, job_id: str) -> bool:
        """Return a running job to a clean pending state."""
        async with self._connect() as db:
            cursor = await db.execute(
                """
                UPDATE scrape_jobs
                SET status = 'pending', started_at = NULL, finished_at = NULL,
                    error_message = NULL
                WHERE id = ? AND status = 'running'
                """,
                (job_id,),
            )
            await db.commit()
            return cursor.rowcount == 1

    async def delete_jobs(self, ids: list[str]) -> int:
        """Delete terminal jobs; active jobs must be cancelled first."""
        if not ids:
            return 0

        placeholders = ",".join("?" * len(ids))
        async with self._connect() as db:
            cursor = await db.execute(
                f"""
                SELECT COUNT(*) FROM scrape_jobs
                WHERE id IN ({placeholders})
                  AND status IN ('pending', 'running', 'pending_action')
                """,
                ids,
            )
            active_count = int((await cursor.fetchone())[0])
            if active_count:
                raise ValueError(f"有 {active_count} 个任务仍在处理中，请先取消")
            cursor = await db.execute(
                f"DELETE FROM scrape_jobs WHERE id IN ({placeholders})",
                ids,
            )
            await db.commit()
            return cursor.rowcount
