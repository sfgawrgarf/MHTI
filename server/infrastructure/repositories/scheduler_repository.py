"""scheduled_tasks 表仓储。"""

from __future__ import annotations

import aiosqlite
from datetime import datetime, timedelta
from croniter import croniter

from server.infrastructure.repositories.base import BaseRepository


class SchedulerRepository(BaseRepository):
    """定时任务表访问。"""

    async def recover_running(self, now: datetime) -> None:
        await self._execute(
            "UPDATE scheduled_tasks SET "
            "last_status = CASE WHEN enabled = 1 THEN 'retry' ELSE 'cancelled' END, "
            "next_run = CASE WHEN enabled = 1 THEN ? ELSE NULL END, "
            "running_for = CASE WHEN enabled = 1 THEN running_for ELSE NULL END "
            "WHERE last_status = 'running'",
            (now.isoformat(),),
        )

    async def claim_due(self, now: datetime) -> dict | None:
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            cursor = await db.execute(
                "SELECT * FROM scheduled_tasks WHERE enabled = 1 AND next_run <= ? "
                "AND COALESCE(last_status, '') != 'running' ORDER BY next_run LIMIT 1",
                (now.isoformat(),),
            )
            row = await cursor.fetchone()
            if row is None:
                await db.rollback()
                return None
            item = dict(row)
            occurrence = item.get("running_for") or item["next_run"]
            retries = item.get("retry_count", 0) if item.get("running_for") else 0
            await db.execute(
                "UPDATE scheduled_tasks SET last_status = 'running', last_attempt = ?, "
                "running_for = ?, retry_count = ? WHERE id = ?",
                (now.isoformat(), occurrence, retries, item["id"]),
            )
            await db.commit()
            return {**item, "running_for": occurrence, "retry_count": retries}

    async def finish_run(self, claimed: dict, now: datetime, error: str | None) -> None:
        """Use current Cron/enabled values so edits during dispatch take effect."""
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            cursor = await db.execute("SELECT * FROM scheduled_tasks WHERE id = ?", (claimed["id"],))
            row = await cursor.fetchone()
            if row is None or row["running_for"] != claimed["running_for"]:
                await db.rollback()
                return
            enabled = bool(row["enabled"])
            changed = row["cron_expression"] != claimed["cron_expression"]
            retries = int(row["retry_count"] or 0) + 1 if error else 0
            retry = bool(error and enabled and not changed and retries <= 3)
            next_run = None
            if enabled:
                next_run = (
                    now + timedelta(seconds=60 * 2 ** (retries - 1))
                    if retry else croniter(row["cron_expression"], now).get_next(datetime)
                ).isoformat()
            await db.execute(
                "UPDATE scheduled_tasks SET last_status = ?, last_error = ?, retry_count = ?, "
                "last_run = CASE WHEN ? IS NULL THEN ? ELSE last_run END, "
                "next_run = ?, running_for = ? WHERE id = ?",
                (
                    "retry" if retry else ("failed" if error else "success"),
                    error, retries, error, now.isoformat(), next_run,
                    claimed["running_for"] if retry else None, claimed["id"],
                ),
            )
            await db.commit()

    async def insert_task(
        self,
        *,
        task_id: str,
        name: str,
        folder_path: str,
        cron_expression: str,
        enabled: bool,
        next_run: str | None,
        created_at: str,
    ) -> None:
        async with self._connect() as db:
            await db.execute(
                """
                INSERT INTO scheduled_tasks
                    (id, name, folder_path, cron_expression, enabled, next_run, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (task_id, name, folder_path, cron_expression, enabled, next_run, created_at),
            )
            await db.commit()

    async def get_task(self, task_id: str) -> aiosqlite.Row | None:
        return await self._fetch_one(
            "SELECT * FROM scheduled_tasks WHERE id = ?", (task_id,)
        )

    async def list_tasks(self) -> list[aiosqlite.Row]:
        return await self._fetch_all(
            "SELECT * FROM scheduled_tasks ORDER BY created_at DESC"
        )

    async def update_task(
        self,
        *,
        task_id: str,
        name: str,
        folder_path: str,
        cron_expression: str,
        enabled: bool,
        next_run: str | None,
    ) -> None:
        async with self._connect() as db:
            await db.execute(
                """
                UPDATE scheduled_tasks
                SET name = ?, folder_path = ?, cron_expression = ?, enabled = ?, next_run = ?
                WHERE id = ?
                """,
                (name, folder_path, cron_expression, enabled, next_run, task_id),
            )
            await db.commit()

    async def delete_task(self, task_id: str) -> bool:
        return await self._execute(
            "DELETE FROM scheduled_tasks WHERE id = ?", (task_id,)
        ) > 0
