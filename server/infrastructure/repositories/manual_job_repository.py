"""manual_jobs 表仓储。"""

from __future__ import annotations

from datetime import datetime

import aiosqlite

from server.infrastructure.repositories.base import BaseRepository
from server.models.manual_job import ManualJobCreate, ManualJobStatus

_MANUAL_JOBS_DDL = """
    CREATE TABLE IF NOT EXISTS manual_jobs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        schedule_key TEXT UNIQUE,
        scan_path TEXT NOT NULL,
        target_folder TEXT NOT NULL,
        metadata_dir TEXT DEFAULT '',
        link_mode INTEGER NOT NULL DEFAULT 2,
        delete_empty_parent INTEGER DEFAULT 1,
        config_reuse_id INTEGER,
        source TEXT DEFAULT 'manual',
        advanced_settings TEXT,
        scan_locator TEXT,
        target_locator TEXT,
        metadata_locator TEXT,
        allow_local_output INTEGER DEFAULT 0,
        created_at TEXT NOT NULL,
        started_at TEXT,
        finished_at TEXT,
        status TEXT NOT NULL DEFAULT 'pending',
        success_count INTEGER DEFAULT 0,
        skip_count INTEGER DEFAULT 0,
        error_count INTEGER DEFAULT 0,
        total_count INTEGER DEFAULT 0,
        dispatched_count INTEGER,
        error_message TEXT
    )
"""


class ManualJobRepository(BaseRepository):
    """手动任务表访问。"""

    async def _ensure_custom_schema(self, db: aiosqlite.Connection) -> None:
        await db.execute(_MANUAL_JOBS_DDL)

    async def ensure_schema(self) -> None:
        """兼容旧数据库：补齐缺失列（幂等）。"""
        async with self._connect() as db:
            async with db.execute("PRAGMA table_info(manual_jobs)") as cursor:
                columns = {row[1] for row in await cursor.fetchall()}
            if "schedule_key" not in columns:
                await db.execute("ALTER TABLE manual_jobs ADD COLUMN schedule_key TEXT")
            if "dispatched_count" not in columns:
                await db.execute("ALTER TABLE manual_jobs ADD COLUMN dispatched_count INTEGER")
            await db.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_manual_jobs_schedule_key "
                "ON manual_jobs(schedule_key)"
            )
            try:
                await db.execute("ALTER TABLE manual_jobs ADD COLUMN metadata_dir TEXT DEFAULT ''")
            except Exception:
                pass  # 列已存在
            try:
                await db.execute("ALTER TABLE manual_jobs ADD COLUMN source TEXT DEFAULT 'manual'")
            except Exception:
                pass  # 列已存在
            try:
                await db.execute("ALTER TABLE manual_jobs ADD COLUMN advanced_settings TEXT")
            except Exception:
                pass  # 列已存在
            try:
                await db.execute("ALTER TABLE manual_jobs ADD COLUMN scan_locator TEXT")
            except Exception:
                pass  # 列已存在
            try:
                await db.execute("ALTER TABLE manual_jobs ADD COLUMN target_locator TEXT")
            except Exception:
                pass  # 列已存在
            try:
                await db.execute("ALTER TABLE manual_jobs ADD COLUMN metadata_locator TEXT")
            except Exception:
                pass  # 列已存在
            try:
                await db.execute("ALTER TABLE manual_jobs ADD COLUMN allow_local_output INTEGER DEFAULT 0")
            except Exception:
                pass  # 列已存在
            await db.commit()

    async def insert_job(
        self,
        data: ManualJobCreate,
        *,
        created_at: str,
        advanced_settings_json: str | None,
        scan_locator_json: str | None,
        target_locator_json: str | None,
        metadata_locator_json: str | None,
    ) -> int:
        """插入手动任务，返回自增 job_id。"""
        async with self._connect() as db:
            cursor = await db.execute(
                """
                INSERT INTO manual_jobs
                (scan_path, target_folder, metadata_dir, link_mode, delete_empty_parent,
                 config_reuse_id, source, advanced_settings, scan_locator, target_locator,
                 metadata_locator, allow_local_output, created_at, status, schedule_key)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(schedule_key) DO NOTHING
                """,
                (
                    data.scan_path,
                    data.target_folder,
                    data.metadata_dir,
                    data.link_mode.value,
                    1 if data.delete_empty_parent else 0,
                    data.config_reuse_id,
                    data.source.value,
                    advanced_settings_json,
                    scan_locator_json,
                    target_locator_json,
                    metadata_locator_json,
                    1 if data.allow_local_output else 0,
                    created_at,
                    ManualJobStatus.PENDING.value,
                    data.schedule_key,
                ),
            )
            if cursor.rowcount == 0 and data.schedule_key:
                cursor = await db.execute(
                    "SELECT id FROM manual_jobs WHERE schedule_key = ?", (data.schedule_key,),
                )
                job_id = int((await cursor.fetchone())[0])
            else:
                job_id = cursor.lastrowid
            await db.commit()
            return job_id

    async def list_jobs(
        self,
        limit: int = 20,
        offset: int = 0,
        search: str | None = None,
        status: ManualJobStatus | None = None,
    ) -> tuple[list[aiosqlite.Row], int]:
        conditions = []
        params = []

        if search:
            conditions.append("(scan_path LIKE ? OR target_folder LIKE ?)")
            params.extend([f"%{search}%", f"%{search}%"])

        if status:
            conditions.append("status = ?")
            params.append(status.value)

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        async with self._connect() as db:
            # Get total count
            cursor = await db.execute(
                f"SELECT COUNT(*) as count FROM manual_jobs {where_clause}",
                params,
            )
            row = await cursor.fetchone()
            total = row["count"] if row else 0

            # Get records
            cursor = await db.execute(
                f"""
                SELECT * FROM manual_jobs {where_clause}
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?
                """,
                params + [limit, offset],
            )
            rows = await cursor.fetchall()

        return list(rows), total

    async def get_job_raw(self, job_id: int) -> aiosqlite.Row | None:
        return await self._fetch_one(
            "SELECT * FROM manual_jobs WHERE id = ?", (job_id,)
        )

    async def prepare_recovery(self) -> list[int]:
        """Reset interrupted manual scans and return pending IDs."""
        candidates = await self._fetch_all(
            "SELECT id FROM manual_jobs WHERE (status = 'running' AND dispatched_count IS NOT NULL) "
            "OR (status = 'success' AND dispatched_count IS NULL)",
        )
        for row in candidates:
            await self.refresh_progress(int(row[0]))
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            await db.execute(
                """
                UPDATE manual_jobs
                SET status = 'pending', started_at = NULL, finished_at = NULL,
                    error_message = NULL
                WHERE status = 'running' AND dispatched_count IS NULL
                """
            )
            cursor = await db.execute(
                """
                SELECT id FROM manual_jobs
                WHERE status = 'pending'
                ORDER BY created_at ASC
                """
            )
            rows = await cursor.fetchall()
            await db.commit()
        return [int(row[0]) for row in rows]

    async def get_dispatched_paths(self, job_id: int) -> set[str]:
        """Recognize work accepted before an interrupted scan resumes."""
        rows = await self._fetch_all(
            "SELECT file_path FROM scrape_jobs WHERE source = 'manual' AND source_id = ? "
            "AND replaces_job_id IS NULL AND operation != 'rematch' "
            "UNION SELECT folder_path FROM history_records WHERE manual_job_id = ? "
            "AND scrape_job_id IS NULL AND status IN ('skipped', 'deleted')",
            (job_id, job_id),
        )
        return {str(row[0]) for row in rows}

    async def refresh_progress(self, job_id: int) -> None:
        """Recompute outcomes atomically; never add counters on retries.

        Adapted from upstream v2.0.1. Continuation histories count once. Failed
        correction attempts leave the original successful history authoritative.
        """
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            cursor = await db.execute("SELECT * FROM manual_jobs WHERE id = ?", (job_id,))
            job = await cursor.fetchone()
            if job is None or job['status'] == 'pending':
                await db.rollback()
                return
            cursor = await db.execute(
                """
                SELECT status, COUNT(*) FROM (
                    SELECT h.status FROM history_records h
                    LEFT JOIN scrape_jobs j ON j.id = h.scrape_job_id
                    WHERE h.manual_job_id = ? AND h.total_files > 0 AND h.status != 'replaced'
                      AND (COALESCE(j.operation, '') != 'rematch' OR h.status = 'success')
                    UNION ALL
                    SELECT j.status FROM scrape_jobs j
                    WHERE j.source = 'manual' AND j.source_id = ?
                      AND j.operation != 'rematch' AND j.status != 'replaced'
                      AND j.replaced_by_job_id IS NULL
                      AND NOT EXISTS (SELECT 1 FROM history_records h
                                      WHERE h.scrape_job_id = j.id OR h.id = j.continuation_history_id)
                ) GROUP BY status
                """, (job_id, job_id),
            )
            counts = {str(row[0]): int(row[1]) for row in await cursor.fetchall()}
            success = counts.get('success', 0)
            error = sum(counts.get(s, 0) for s in ('failed', 'timeout', 'cancelled'))
            skipped = sum(counts.get(s, 0) for s in ('skipped', 'deleted'))
            dispatched = job['dispatched_count']
            # In the old release SUCCESS meant dispatch finished, even when
            # children were still running. Repair those rows from real results.
            if dispatched is None and job['status'] == 'success' and job['total_count']:
                # Old dispatch skips had no history record. Only count durable
                # child outcomes as dispatched, so the missing files stay skips.
                # The old stored success/skip counters may have drifted on retry.
                dispatched = min(job['total_count'], sum(counts.values()))
            if dispatched is not None:
                skipped += max(0, job['total_count'] - dispatched)
            cursor = await db.execute(
                "SELECT COUNT(*) FROM scrape_jobs WHERE source = 'manual' AND source_id = ? "
                "AND status IN ('pending', 'running', 'pending_action')", (job_id,),
            )
            active = int((await cursor.fetchone())[0])
            status, finished, message = job['status'], job['finished_at'], job['error_message']
            if dispatched is not None and status != 'cancelled':
                unsettled = active or counts.get('running', 0) or counts.get('pending_action', 0)
                if unsettled or success + error + skipped < job['total_count']:
                    status, finished, message = 'running', None, None
                else:
                    status = 'failed' if error and not success + skipped else 'success'
                    finished = finished or datetime.now().isoformat()
                    message = '全部文件处理失败' if status == 'failed' else None
            await db.execute(
                "UPDATE manual_jobs SET success_count = ?, error_count = ?, skip_count = ?, "
                "dispatched_count = ?, status = ?, finished_at = ?, error_message = ? WHERE id = ?",
                (success, error, skipped, dispatched, status, finished, message, job_id),
            )
            await db.commit()

    async def claim_job(self, job_id: int) -> bool:
        """Atomically claim a pending manual job for one worker."""
        async with self._connect() as db:
            cursor = await db.execute(
                """
                UPDATE manual_jobs
                SET status = ?, started_at = ?
                WHERE id = ? AND status = ?
                """,
                (
                    ManualJobStatus.RUNNING.value,
                    datetime.now().isoformat(),
                    job_id,
                    ManualJobStatus.PENDING.value,
                ),
            )
            await db.commit()
            return cursor.rowcount == 1

    async def get_child_counts(
        self,
        job_ids: list[int],
    ) -> dict[int, dict[str, int]]:
        """Load scrape-job status counts for manual jobs in one query."""
        if not job_ids:
            return {}
        placeholders = ",".join("?" * len(job_ids))
        rows = await self._fetch_all(
            f"""
            SELECT source_id, status, COUNT(*) AS count
            FROM scrape_jobs
            WHERE source = 'manual' AND source_id IN ({placeholders})
            GROUP BY source_id, status
            """,
            tuple(job_ids),
        )
        counts: dict[int, dict[str, int]] = {}
        for row in rows:
            counts.setdefault(int(row["source_id"]), {})[str(row["status"])] = int(row["count"])
        return counts

    async def delete_jobs(self, ids: list[int]) -> int:
        """删除任务并级联删除关联的刮削记录（history_records.manual_job_id）。"""
        if not ids:
            return 0

        placeholders = ",".join("?" * len(ids))
        async with self._connect() as db:
            cursor = await db.execute(
                f"""
                SELECT COUNT(*) FROM manual_jobs
                WHERE id IN ({placeholders}) AND status IN ('pending', 'running')
                """,
                ids,
            )
            active_count = int((await cursor.fetchone())[0])
            cursor = await db.execute(
                f"""
                SELECT COUNT(*) FROM scrape_jobs
                WHERE source = 'manual' AND source_id IN ({placeholders})
                  AND status IN ('pending', 'running', 'pending_action')
                """,
                ids,
            )
            active_child_count = int((await cursor.fetchone())[0])
            if active_count or active_child_count:
                raise ValueError(
                    "手动任务或其刮削子任务仍在处理中，请先取消"
                )
            # 级联删除该手动任务投递的刮削任务，避免留下孤立队列记录。
            await db.execute(
                f"DELETE FROM scrape_jobs WHERE source = 'manual' AND source_id IN ({placeholders})",
                ids,
            )
            # 级联删除关联的刮削记录
            await db.execute(
                f"DELETE FROM history_records WHERE manual_job_id IN ({placeholders})",
                ids,
            )
            cursor = await db.execute(
                f"DELETE FROM manual_jobs WHERE id IN ({placeholders})",
                ids,
            )
            await db.commit()
            return cursor.rowcount

    async def update_job_status(
        self,
        job_id: int,
        status: ManualJobStatus,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
        success_count: int | None = None,
        skip_count: int | None = None,
        error_count: int | None = None,
        total_count: int | None = None,
        dispatched_count: int | None = None,
        error_message: str | None = None,
        expected_status: ManualJobStatus | None = None,
    ) -> bool:
        updates = ["status = ?"]
        params = [status.value]

        if started_at is not None:
            updates.append("started_at = ?")
            params.append(started_at.isoformat())
        if finished_at is not None:
            updates.append("finished_at = ?")
            params.append(finished_at.isoformat())
        if success_count is not None:
            updates.append("success_count = ?")
            params.append(success_count)
        if skip_count is not None:
            updates.append("skip_count = ?")
            params.append(skip_count)
        if error_count is not None:
            updates.append("error_count = ?")
            params.append(error_count)
        if total_count is not None:
            updates.append("total_count = ?")
            params.append(total_count)
        if dispatched_count is not None:
            updates.append("dispatched_count = ?")
            params.append(dispatched_count)
        if error_message is not None:
            updates.append("error_message = ?")
            params.append(error_message)

        params.append(job_id)
        where_clause = "id = ?"
        if expected_status is not None:
            where_clause += " AND status = ?"
            params.append(expected_status.value)

        async with self._connect() as db:
            cursor = await db.execute(
                f"UPDATE manual_jobs SET {', '.join(updates)} WHERE {where_clause}",
                params,
            )
            await db.commit()
            return cursor.rowcount == 1
