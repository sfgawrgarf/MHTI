"""Scheduler service for managing scheduled tasks."""

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path

import aiosqlite
from croniter import croniter

from server.core.db.connection import db_connection
from server.core.database import DATABASE_PATH
from server.models.scheduler import (
    ScheduledTask,
    ScheduledTaskCreate,
    ScheduledTaskUpdate,
)

logger = logging.getLogger(__name__)
ScheduledTaskExecutor = Callable[[ScheduledTask], Awaitable[None]]


class SchedulerService:
    """Service for managing scheduled scraping tasks."""

    def __init__(
        self,
        db_path: Path | None = None,
        *,
        executor: ScheduledTaskExecutor | None = None,
        poll_interval: float = 30.0,
    ):
        """Initialize scheduler service."""
        self.db_path = db_path or DATABASE_PATH
        self._executor = executor
        self._poll_interval = max(0.1, poll_interval)
        self._worker_task: asyncio.Task[None] | None = None
        self._wake_event: asyncio.Event | None = None
        self._run_lock = asyncio.Lock()
        self._db_lock = asyncio.Lock()
        self._db_ready = False
        self._stopping = False

    async def _ensure_db(self) -> None:
        """Ensure the scheduler table exists for app and isolated test databases."""
        if self._db_ready:
            return
        async with self._db_lock:
            if self._db_ready:
                return
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            async with db_connection(self.db_path) as db:
                await db.execute(
                    """CREATE TABLE IF NOT EXISTS scheduled_tasks (
                        id TEXT PRIMARY KEY,
                        name TEXT NOT NULL,
                        folder_path TEXT NOT NULL,
                        cron_expression TEXT NOT NULL,
                        enabled INTEGER DEFAULT 1,
                        last_run TEXT,
                        next_run TEXT,
                        created_at TEXT NOT NULL
                    )"""
                )
                await db.execute(
                    """CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_due
                       ON scheduled_tasks(enabled, next_run)"""
                )
                await db.commit()
            self._db_ready = True

    def _calculate_next_run(
        self,
        cron_expression: str,
        base_time: datetime | None = None,
    ) -> datetime | None:
        """Calculate next run time from cron expression."""
        try:
            cron = croniter(cron_expression, base_time or datetime.now())
            return cron.get_next(datetime)
        except (ValueError, KeyError):
            return None

    def _validate_cron(self, cron_expression: str) -> bool:
        """Validate cron expression."""
        try:
            croniter(cron_expression)
            return True
        except (ValueError, KeyError):
            return False

    async def create_task(self, task: ScheduledTaskCreate) -> ScheduledTask:
        """Create a new scheduled task."""
        await self._ensure_db()
        if not self._validate_cron(task.cron_expression):
            raise ValueError("无效的 Cron 表达式")

        task_id = str(uuid.uuid4())[:8]
        now = datetime.now()
        next_run = self._calculate_next_run(task.cron_expression) if task.enabled else None

        async with db_connection(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO scheduled_tasks (id, name, folder_path, cron_expression, enabled, next_run, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    task.name,
                    task.folder_path,
                    task.cron_expression,
                    1 if task.enabled else 0,
                    next_run.isoformat() if next_run else None,
                    now.isoformat(),
                ),
            )
            await db.commit()

        created = ScheduledTask(
            id=task_id,
            name=task.name,
            folder_path=task.folder_path,
            cron_expression=task.cron_expression,
            enabled=task.enabled,
            next_run=next_run,
            created_at=now,
        )
        self._notify_worker()
        return created

    async def get_task(self, task_id: str) -> ScheduledTask | None:
        """Get a scheduled task by ID."""
        await self._ensure_db()

        async with db_connection(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM scheduled_tasks WHERE id = ?",
                (task_id,),
            )
            row = await cursor.fetchone()

        if row is None:
            return None

        return self._row_to_task(row)

    async def list_tasks(self) -> list[ScheduledTask]:
        """List all scheduled tasks."""
        await self._ensure_db()

        async with db_connection(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM scheduled_tasks ORDER BY created_at DESC"
            )
            rows = await cursor.fetchall()

        return [self._row_to_task(row) for row in rows]

    async def update_task(self, task_id: str, update: ScheduledTaskUpdate) -> ScheduledTask | None:
        """Update a scheduled task."""
        task = await self.get_task(task_id)
        if task is None:
            return None

        # Apply updates
        if update.name is not None:
            task.name = update.name
        if update.folder_path is not None:
            task.folder_path = update.folder_path
        if update.cron_expression is not None:
            if not self._validate_cron(update.cron_expression):
                raise ValueError("无效的 Cron 表达式")
            task.cron_expression = update.cron_expression
        if update.enabled is not None:
            task.enabled = update.enabled

        # Recalculate next run
        next_run = self._calculate_next_run(task.cron_expression) if task.enabled else None

        async with db_connection(self.db_path) as db:
            await db.execute(
                """
                UPDATE scheduled_tasks
                SET name = ?, folder_path = ?, cron_expression = ?, enabled = ?, next_run = ?
                WHERE id = ?
                """,
                (
                    task.name,
                    task.folder_path,
                    task.cron_expression,
                    1 if task.enabled else 0,
                    next_run.isoformat() if next_run else None,
                    task_id,
                ),
            )
            await db.commit()

        task.next_run = next_run
        self._notify_worker()
        return task

    async def delete_task(self, task_id: str) -> bool:
        """Delete a scheduled task."""
        await self._ensure_db()

        async with db_connection(self.db_path) as db:
            cursor = await db.execute(
                "DELETE FROM scheduled_tasks WHERE id = ?",
                (task_id,),
            )
            await db.commit()
            deleted = cursor.rowcount > 0
        if deleted:
            self._notify_worker()
        return deleted

    async def toggle_task(self, task_id: str) -> ScheduledTask | None:
        """Toggle task enabled status."""
        task = await self.get_task(task_id)
        if task is None:
            return None

        return await self.update_task(task_id, ScheduledTaskUpdate(enabled=not task.enabled))

    async def start(self) -> None:
        """Start the single scheduler loop for this application process."""
        if self._worker_task is not None and not self._worker_task.done():
            return
        self._stopping = False
        self._wake_event = asyncio.Event()
        self._worker_task = asyncio.create_task(
            self._run_loop(),
            name="scheduled-task-runner",
        )

    async def stop(self) -> None:
        """Stop the scheduler loop without cancelling queued manual jobs."""
        task = self._worker_task
        if task is None:
            return
        self._stopping = True
        self._notify_worker()
        try:
            await task
        finally:
            if self._worker_task is task:
                self._worker_task = None
                self._wake_event = None

    def _notify_worker(self) -> None:
        if self._wake_event is not None:
            self._wake_event.set()

    async def _run_loop(self) -> None:
        while not self._stopping:
            wake_event = self._wake_event
            if wake_event is None:
                return
            wake_event.clear()
            try:
                await self.run_due_tasks()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Scheduled task polling failed; retrying")
            if self._stopping:
                return
            try:
                await asyncio.wait_for(
                    wake_event.wait(),
                    timeout=self._poll_interval,
                )
            except TimeoutError:
                pass

    async def run_due_tasks(self, now: datetime | None = None) -> int:
        """Atomically claim and execute every task due at ``now``."""
        await self._ensure_db()
        current_time = now or datetime.now()
        claimed: list[ScheduledTask] = []

        async with self._run_lock:
            async with db_connection(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                await db.execute("BEGIN IMMEDIATE")
                cursor = await db.execute(
                    """SELECT * FROM scheduled_tasks
                       WHERE enabled = 1 AND next_run IS NOT NULL AND next_run <= ?
                       ORDER BY next_run ASC""",
                    (current_time.isoformat(),),
                )
                rows = await cursor.fetchall()
                for row in rows:
                    task = self._row_to_task(row)
                    next_run = self._calculate_next_run(
                        task.cron_expression,
                        current_time,
                    )
                    if next_run is None:
                        await db.execute(
                            "UPDATE scheduled_tasks SET enabled = 0, next_run = NULL WHERE id = ?",
                            (task.id,),
                        )
                        logger.error("Disabled scheduled task with invalid cron: %s", task.id)
                        continue
                    update = await db.execute(
                        """UPDATE scheduled_tasks SET next_run = ?
                           WHERE id = ? AND enabled = 1 AND next_run = ?""",
                        (next_run.isoformat(), task.id, row["next_run"]),
                    )
                    if update.rowcount:
                        task.next_run = next_run
                        claimed.append(task)
                await db.commit()

        for task in claimed:
            try:
                await self._execute_task(task)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Scheduled task failed: %s (%s)", task.name, task.id)
                continue

            finished_at = datetime.now()
            async with db_connection(self.db_path) as db:
                await db.execute(
                    "UPDATE scheduled_tasks SET last_run = ? WHERE id = ?",
                    (finished_at.isoformat(), task.id),
                )
                await db.commit()

        return len(claimed)

    async def _execute_task(self, task: ScheduledTask) -> None:
        if self._executor is not None:
            await self._executor(task)
            return

        from server.models.manual_job import LinkMode, ManualJobCreate
        from server.models.organize import OrganizeMode
        from server.services.config_service import ConfigService
        from server.services.manual_job_service import ManualJobService

        config = await ConfigService(db_path=self.db_path).get_organize_config()
        if not config.organize_dir.strip():
            raise RuntimeError("未配置整理目录，无法执行定时任务")
        mode_map = {
            OrganizeMode.HARDLINK: LinkMode.HARDLINK,
            OrganizeMode.MOVE: LinkMode.MOVE,
            OrganizeMode.COPY: LinkMode.COPY,
            OrganizeMode.SYMLINK: LinkMode.SYMLINK,
        }
        await ManualJobService(db_path=self.db_path).create_job(
            ManualJobCreate(
                scan_path=task.folder_path,
                target_folder=config.organize_dir,
                metadata_dir=config.metadata_dir,
                link_mode=mode_map[config.organize_mode],
                delete_empty_parent=config.auto_clean_source,
            )
        )

    def _row_to_task(self, row) -> ScheduledTask:
        """Convert database row to ScheduledTask."""
        return ScheduledTask(
            id=row["id"],
            name=row["name"],
            folder_path=row["folder_path"],
            cron_expression=row["cron_expression"],
            enabled=bool(row["enabled"]),
            last_run=datetime.fromisoformat(row["last_run"]) if row["last_run"] else None,
            next_run=datetime.fromisoformat(row["next_run"]) if row["next_run"] else None,
            created_at=datetime.fromisoformat(row["created_at"]) if row["created_at"] else None,
        )
