"""Scheduler service for managing scheduled tasks."""

import uuid
import asyncio
import logging
from datetime import datetime
from pathlib import Path

from croniter import croniter

from server.infrastructure.db import DATABASE_PATH
from server.infrastructure.repositories.scheduler_repository import SchedulerRepository
from server.models.scheduler import (
    ScheduledTask,
    ScheduledTaskCreate,
    ScheduledTaskUpdate,
)

logger = logging.getLogger(__name__)


class SchedulerService:
    """Service for managing scheduled scraping tasks."""

    def __init__(self, db_path: Path | None = None):
        """Initialize scheduler service."""
        self.db_path = db_path or DATABASE_PATH
        self._repo = SchedulerRepository(db_path)
        self._task: asyncio.Task | None = None
        self._pending_finishes: dict[str, tuple[dict, datetime, str | None]] = {}

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        await self._repo.recover_running(datetime.now())
        self._task = asyncio.create_task(self._run(), name="mhti-scheduler")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def _run(self) -> None:
        last_backup_cleanup = None
        cleanup_interval = 3600
        while True:
            try:
                if last_backup_cleanup is None or (datetime.now() - last_backup_cleanup).total_seconds() >= cleanup_interval:
                    from server.application.correction_service import cleanup_expired_correction_backups
                    last_backup_cleanup = datetime.now()
                    try:
                        await cleanup_expired_correction_backups()
                        cleanup_interval = 3600
                    except Exception:
                        cleanup_interval = 60
                        logger.exception("纠正备份清理失败，60 秒后重试")
                await self.tick_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("定时调度失败，将在下一轮重试")
            await asyncio.sleep(1)

    async def tick_once(self, now: datetime | None = None) -> None:
        now = now or datetime.now()
        # Retry completion writes without dispatching the same occurrence again.
        for task_id, completion in list(self._pending_finishes.items()):
            await self._repo.finish_run(*completion)
            self._pending_finishes.pop(task_id, None)
        for _ in range(20):
            claimed = await self._repo.claim_due(now)
            if claimed is None:
                return
            error = None
            try:
                await self._dispatch(claimed)
            except Exception as exc:
                error = str(exc) or type(exc).__name__
                logger.exception("定时任务投递失败: %s", claimed["id"])
            self._pending_finishes[claimed["id"]] = (claimed, now, error)
            await self._repo.finish_run(claimed, now, error)
            self._pending_finishes.pop(claimed["id"], None)

    async def _dispatch(self, claimed: dict) -> None:
        from server.application.manual_job_service import ManualJobService
        from server.domain.system.config_service import ConfigService
        from server.models.manual_job import ManualJobCreate, LinkMode
        from server.models.storage import StorageLocator, StorageProvider, is_p115_virtual_path

        config_service = ConfigService(db_path=self.db_path)
        config = await config_service.get_organize_config()
        if not config.organize_dir:
            raise ValueError("定时任务执行前请先设置整理输出目录")
        path = claimed["folder_path"]
        scan_locator = None
        is_cloud = is_p115_virtual_path(path)
        if is_cloud:
            from server.domain.integration.p115_service import P115Service
            file_id = await P115Service(config_service).resolve_directory_id_by_path(path)
            scan_locator = StorageLocator(provider=StorageProvider.P115, path=path, file_id=file_id)
        modes = {"copy": LinkMode.COPY, "move": LinkMode.MOVE, "hardlink": LinkMode.HARDLINK, "symlink": LinkMode.SYMLINK}
        local_download = is_cloud and not is_p115_virtual_path(config.organize_dir)
        job = ManualJobCreate(
            scan_path=path, target_folder=config.organize_dir,
            scan_locator=scan_locator, metadata_dir=config.metadata_dir,
            link_mode=LinkMode.COPY if local_download else modes[config.organize_mode.value],
            allow_local_output=local_download,
            delete_empty_parent=config.auto_clean_source,
            schedule_key=f"{claimed['id']}:{claimed['running_for']}",
        )
        await ManualJobService(db_path=self.db_path).create_job(job)

    def _calculate_next_run(self, cron_expression: str) -> datetime | None:
        """Calculate next run time from cron expression."""
        try:
            cron = croniter(cron_expression, datetime.now())
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
        if not self._validate_cron(task.cron_expression):
            raise ValueError("无效的 Cron 表达式")
        task_id = str(uuid.uuid4())[:8]
        now = datetime.now()
        next_run = self._calculate_next_run(task.cron_expression) if task.enabled else None

        await self._repo.insert_task(
            task_id=task_id,
            name=task.name,
            folder_path=task.folder_path,
            cron_expression=task.cron_expression,
            enabled=1 if task.enabled else 0,
            next_run=next_run.isoformat() if next_run else None,
            created_at=now.isoformat(),
        )

        return ScheduledTask(
            id=task_id,
            name=task.name,
            folder_path=task.folder_path,
            cron_expression=task.cron_expression,
            enabled=task.enabled,
            next_run=next_run,
            created_at=now,
        )

    async def get_task(self, task_id: str) -> ScheduledTask | None:
        """Get a scheduled task by ID."""
        row = await self._repo.get_task(task_id)
        if row is None:
            return None

        return self._row_to_task(row)

    async def list_tasks(self) -> list[ScheduledTask]:
        """List all scheduled tasks."""
        rows = await self._repo.list_tasks()
        return [self._row_to_task(row) for row in rows]

    async def update_task(self, task_id: str, update: ScheduledTaskUpdate) -> ScheduledTask | None:
        """Update a scheduled task."""
        task = await self.get_task(task_id)
        if task is None:
            return None

        # Apply updates
        if update.cron_expression is not None and not self._validate_cron(update.cron_expression):
            raise ValueError("无效的 Cron 表达式")
        if update.name is not None:
            task.name = update.name
        if update.folder_path is not None:
            task.folder_path = update.folder_path
        if update.cron_expression is not None:
            task.cron_expression = update.cron_expression
        if update.enabled is not None:
            task.enabled = update.enabled

        # Recalculate next run
        next_run = self._calculate_next_run(task.cron_expression) if task.enabled else None

        await self._repo.update_task(
            task_id=task_id,
            name=task.name,
            folder_path=task.folder_path,
            cron_expression=task.cron_expression,
            enabled=1 if task.enabled else 0,
            next_run=next_run.isoformat() if next_run else None,
        )

        task.next_run = next_run
        return task

    async def delete_task(self, task_id: str) -> bool:
        """Delete a scheduled task."""
        return await self._repo.delete_task(task_id)

    async def toggle_task(self, task_id: str) -> ScheduledTask | None:
        """Toggle task enabled status."""
        task = await self.get_task(task_id)
        if task is None:
            return None

        return await self.update_task(task_id, ScheduledTaskUpdate(enabled=not task.enabled))

    def _row_to_task(self, row) -> ScheduledTask:
        """Convert database row to ScheduledTask."""
        return ScheduledTask(
            id=row["id"],
            name=row["name"],
            folder_path=row["folder_path"],
            cron_expression=row["cron_expression"],
            enabled=bool(row["enabled"]),
            last_run=datetime.fromisoformat(row["last_run"]) if row["last_run"] else None,
            last_attempt=datetime.fromisoformat(row["last_attempt"]) if row["last_attempt"] else None,
            last_status=row["last_status"],
            last_error=row["last_error"],
            retry_count=int(row["retry_count"] or 0),
            next_run=datetime.fromisoformat(row["next_run"]) if row["next_run"] else None,
            created_at=datetime.fromisoformat(row["created_at"]) if row["created_at"] else None,
        )
