"""Manual job service for managing manual scrape tasks."""

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from weakref import WeakKeyDictionary

import aiosqlite

from server.core.db.connection import DatabaseManager, db_connection
from server.core.db.schema import migrate_manual_jobs_table
from server.core.database import DATABASE_PATH
from server.models.manual_job import (
    JobSource,
    LinkMode,
    ManualJob,
    ManualJobAdvancedSettings,
    ManualJobCreate,
    ManualJobStatus,
)
from server.models.organize import OrganizeMode
from server.models.storage import StorageLocator, StorageProvider

logger = logging.getLogger(__name__)


def _link_mode_to_organize_mode(link_mode: LinkMode) -> OrganizeMode:
    """将 LinkMode 转换为 OrganizeMode"""
    mapping = {
        LinkMode.HARDLINK: OrganizeMode.HARDLINK,
        LinkMode.MOVE: OrganizeMode.MOVE,
        LinkMode.COPY: OrganizeMode.COPY,
        LinkMode.SYMLINK: OrganizeMode.SYMLINK,
    }
    return mapping.get(link_mode, OrganizeMode.MOVE)


def _build_file_locator_from_scan(
    scan_locator: StorageLocator,
    scanned_file,
    file_path: str,
) -> StorageLocator:
    """根据扫描结果构造单文件的 115 StorageLocator。"""
    return StorageLocator(
        provider=StorageProvider.P115,
        path=file_path,
        file_id=scanned_file.file_id or scan_locator.file_id,
        parent_id=scanned_file.parent_id or scan_locator.parent_id,
        is_dir=False,
    )


def _serialize_locator(locator: StorageLocator | None) -> str | None:
    """序列化存储定位信息。"""
    if locator is None:
        return None
    return json.dumps(locator.model_dump(mode="json"))


def _deserialize_locator(payload: str | None) -> StorageLocator | None:
    """反序列化存储定位信息。"""
    if not payload:
        return None
    try:
        return StorageLocator(**json.loads(payload))
    except (json.JSONDecodeError, ValueError, TypeError):
        return None

# 任务队列
_job_queue: asyncio.Queue[int] = asyncio.Queue()
_worker_task: asyncio.Task | None = None
_active_job_tasks: dict[int, asyncio.Task] = {}
_user_cancel_requests: set[int] = set()
_job_state_locks: WeakKeyDictionary = WeakKeyDictionary()
_workers_stopping = False


def _get_job_state_lock() -> asyncio.Lock:
    """Return a lock scoped to the current event loop."""
    loop = asyncio.get_running_loop()
    lock = _job_state_locks.get(loop)
    if lock is None:
        lock = asyncio.Lock()
        _job_state_locks[loop] = lock
    return lock


def get_manual_runtime_state(pending_count: int) -> dict[str, int]:
    """Return live manual-worker state without opening a database connection."""
    return {
        "queued_in_memory": min(_job_queue.qsize(), pending_count),
        "active_tasks": sum(not task.done() for task in _active_job_tasks.values()),
        "worker_count": int(_worker_task is not None and not _worker_task.done()),
        "concurrency_limit": 1,
    }


class ManualJobService:
    """Service for managing manual scrape jobs."""

    def __init__(self, db_path: Path | None = None):
        """Initialize manual job service."""
        self.db_path = db_path or DATABASE_PATH
        self._db_ready = False
        self._migration_lock = asyncio.Lock()

    async def _ensure_db(self) -> None:
        """Use startup migrations in production and migrate custom DBs once."""
        if self._db_ready:
            return
        async with self._migration_lock:
            if self._db_ready:
                return
            manager = DatabaseManager._instance
            if (
                self.db_path.resolve() == DATABASE_PATH.resolve()
                and manager is not None
                and manager._initialized
                and manager._loop is asyncio.get_running_loop()
            ):
                self._db_ready = True
                return
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            async with db_connection(self.db_path) as db:
                await db.execute("BEGIN IMMEDIATE")
                await migrate_manual_jobs_table(db)
                await db.commit()
            self._db_ready = True

    async def create_job(self, job: ManualJobCreate) -> ManualJob:
        """Create a new manual job and add to queue."""
        await self._ensure_db()
        now = datetime.now()

        # 序列化高级设置
        advanced_settings_json = None
        if job.advanced_settings is not None:
            advanced_settings_json = json.dumps(job.advanced_settings.model_dump())
        scan_locator_json = _serialize_locator(job.scan_locator)
        target_locator_json = _serialize_locator(job.target_locator)
        metadata_locator_json = _serialize_locator(job.metadata_locator)

        async with db_connection(self.db_path) as db:
            cursor = await db.execute(
                """
                INSERT INTO manual_jobs
                (scan_path, target_folder, metadata_dir, link_mode, delete_empty_parent,
                 config_reuse_id, source, advanced_settings, scan_locator, target_locator,
                 metadata_locator, allow_local_output, created_at, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job.scan_path,
                    job.target_folder,
                    job.metadata_dir,
                    job.link_mode.value,
                    1 if job.delete_empty_parent else 0,
                    job.config_reuse_id,
                    job.source.value,
                    advanced_settings_json,
                    scan_locator_json,
                    target_locator_json,
                    metadata_locator_json,
                    1 if job.allow_local_output else 0,
                    now.isoformat(),
                    ManualJobStatus.PENDING.value,
                ),
            )
            await db.commit()
            job_id = cursor.lastrowid

        created_job = ManualJob(
            id=job_id,
            scan_path=job.scan_path,
            target_folder=job.target_folder,
            metadata_dir=job.metadata_dir,
            scan_locator=job.scan_locator,
            target_locator=job.target_locator,
            metadata_locator=job.metadata_locator,
            allow_local_output=job.allow_local_output,
            link_mode=job.link_mode,
            delete_empty_parent=job.delete_empty_parent,
            config_reuse_id=job.config_reuse_id,
            source=job.source,
            advanced_settings=job.advanced_settings,
            created_at=now,
            status=ManualJobStatus.PENDING,
        )

        # 加入队列
        await _job_queue.put(job_id)
        # 确保 worker 在运行
        _ensure_worker()

        return created_job

    async def prepare_recovery(self) -> list[int]:
        """Reset interrupted manual scans and return persisted pending IDs."""
        await self._ensure_db()
        async with db_connection(self.db_path) as db:
            await db.execute("BEGIN IMMEDIATE")
            await db.execute(
                """
                UPDATE manual_jobs
                SET status = 'pending', started_at = NULL, finished_at = NULL,
                    error_message = NULL
                WHERE status = 'running'
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

    async def claim_job(self, job_id: int) -> bool:
        """Atomically claim a pending manual job for one worker."""
        async with _get_job_state_lock():
            await self._ensure_db()
            async with db_connection(self.db_path) as db:
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

    async def list_jobs(
        self,
        limit: int = 20,
        offset: int = 0,
        search: str | None = None,
        status: ManualJobStatus | None = None,
    ) -> tuple[list[ManualJob], int]:
        """List manual jobs with pagination, search and filter."""
        await self._ensure_db()

        conditions = []
        params = []

        if search:
            conditions.append("(scan_path LIKE ? OR target_folder LIKE ?)")
            params.extend([f"%{search}%", f"%{search}%"])

        if status:
            conditions.append("status = ?")
            params.append(status.value)

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        async with db_connection(self.db_path) as db:
            db.row_factory = aiosqlite.Row

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

        child_counts = await self._get_child_counts([int(row["id"]) for row in rows])
        jobs = [self._row_to_job(row, child_counts.get(int(row["id"]))) for row in rows]
        return jobs, total

    async def get_job(self, job_id: int) -> ManualJob | None:
        """Get a manual job by ID."""
        await self._ensure_db()

        async with db_connection(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM manual_jobs WHERE id = ?",
                (job_id,),
            )
            row = await cursor.fetchone()

        if row is None:
            return None
        child_counts = await self._get_child_counts([job_id])
        return self._row_to_job(row, child_counts.get(job_id))

    async def _get_child_counts(self, job_ids: list[int]) -> dict[int, dict[str, int]]:
        """Load scrape-job state counts for manual jobs in one query."""
        if not job_ids:
            return {}
        placeholders = ",".join("?" * len(job_ids))
        async with db_connection(self.db_path) as db:
            cursor = await db.execute(
                f"""SELECT source_id, status, COUNT(*)
                FROM scrape_jobs
                WHERE source = 'manual' AND source_id IN ({placeholders})
                GROUP BY source_id, status""",
                job_ids,
            )
            rows = await cursor.fetchall()

        counts: dict[int, dict[str, int]] = {}
        for source_id, status, count in rows:
            counts.setdefault(int(source_id), {})[str(status)] = int(count)
        return counts

    async def get_runtime_metrics(self) -> dict:
        """Return persisted queue counts together with in-process worker state."""
        async with db_connection(self.db_path) as db:
            cursor = await db.execute(
                """SELECT status, COUNT(*),
                          MIN(CASE WHEN status = 'pending' THEN created_at END)
                   FROM manual_jobs GROUP BY status"""
            )
            rows = await cursor.fetchall()

        status_counts = {str(row[0]): int(row[1]) for row in rows}
        oldest_value = next((row[2] for row in rows if row[0] == "pending"), None)
        oldest_pending_at = datetime.fromisoformat(oldest_value) if oldest_value else None
        return {
            "status_counts": status_counts,
            **get_manual_runtime_state(status_counts.get("pending", 0)),
            "oldest_pending_at": oldest_pending_at,
            "oldest_pending_seconds": (
                max(0.0, (datetime.now() - oldest_pending_at).total_seconds())
                if oldest_pending_at
                else None
            ),
        }

    async def delete_jobs(self, ids: list[int]) -> int:
        """Delete manual jobs by IDs.

        同时级联删除关联的刮削记录（history_records.manual_job_id）。
        """
        await self._ensure_db()

        if not ids:
            return 0

        placeholders = ",".join("?" * len(ids))
        async with db_connection(self.db_path) as db:
            cursor = await db.execute(
                f"""SELECT COUNT(*) FROM manual_jobs
                WHERE id IN ({placeholders}) AND status IN ('pending', 'running')""",
                ids,
            )
            active_manual = int((await cursor.fetchone())[0])
            cursor = await db.execute(
                f"""SELECT COUNT(*) FROM scrape_jobs
                WHERE source = 'manual' AND source_id IN ({placeholders})
                  AND status IN ('pending', 'running', 'pending_action')""",
                ids,
            )
            active_children = int((await cursor.fetchone())[0])
            if active_manual or active_children:
                raise ValueError("任务或其刮削子任务仍在处理中，请先取消")
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

    async def cancel_job(self, job_id: int) -> tuple[ManualJob | None, bool, int, str]:
        """Cancel a manual scan and every unfinished scrape job it dispatched."""
        from server.services.scrape_job_service import ScrapeJobService

        scrape_service = ScrapeJobService(db_path=self.db_path)
        message = "用户已取消任务及尚未完成的刮削子任务"
        async with _get_job_state_lock():
            job = await self.get_job(job_id)
            if job is None:
                return None, False, 0, "手动任务不存在"
            manual_active = job.status in {ManualJobStatus.PENDING, ManualJobStatus.RUNNING}
            task = _active_job_tasks.get(job_id)
            if job.status == ManualJobStatus.RUNNING and task is not None and not task.done():
                _user_cancel_requests.add(job_id)
            elif manual_active:
                await self.update_job_status(
                    job_id,
                    ManualJobStatus.CANCELLED,
                    finished_at=datetime.now(),
                    error_message=message,
                )

        if job.status == ManualJobStatus.RUNNING and task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        # Stop the dispatcher first so it cannot enqueue more children while the
        # cancellation sweep is in progress.
        cancelled_children = await scrape_service.cancel_jobs_by_source(job_id)
        if not manual_active and cancelled_children == 0:
            return job, False, 0, f"任务已经是 {job.status.value} 状态且没有运行中的子任务"
        if not manual_active:
            await self.update_job_status(
                job_id,
                ManualJobStatus.CANCELLED,
                finished_at=datetime.now(),
                error_message=message,
            )

        updated = await self.get_job(job_id)
        if manual_active and updated is not None and updated.status != ManualJobStatus.CANCELLED:
            await self.update_job_status(
                job_id,
                ManualJobStatus.CANCELLED,
                finished_at=datetime.now(),
                error_message=message,
            )
            updated = await self.get_job(job_id)
        return updated, True, cancelled_children, message

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
        error_message: str | None = None,
    ) -> None:
        """Update job status and counts."""
        await self._ensure_db()

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
        if error_message is not None:
            updates.append("error_message = ?")
            params.append(error_message)

        params.append(job_id)

        async with db_connection(self.db_path) as db:
            await db.execute(
                f"UPDATE manual_jobs SET {', '.join(updates)} WHERE id = ?",
                params,
            )
            await db.commit()

    def _row_to_job(self, row, child_counts: dict[str, int] | None = None) -> ManualJob:
        """Convert database row to ManualJob."""
        # 兼容旧数据，source 可能不存在
        source_value = row["source"] if "source" in row.keys() else "manual"

        # 反序列化高级设置
        advanced_settings = None
        if "advanced_settings" in row.keys() and row["advanced_settings"]:
            try:
                settings_data = json.loads(row["advanced_settings"])
                advanced_settings = ManualJobAdvancedSettings(**settings_data)
            except (json.JSONDecodeError, ValueError):
                pass  # 解析失败则使用 None

        scan_locator = _deserialize_locator(
            row["scan_locator"] if "scan_locator" in row.keys() else None
        )
        target_locator = _deserialize_locator(
            row["target_locator"] if "target_locator" in row.keys() else None
        )
        metadata_locator = _deserialize_locator(
            row["metadata_locator"] if "metadata_locator" in row.keys() else None
        )
        allow_local_output = bool(
            row["allow_local_output"] if "allow_local_output" in row.keys() else 0
        )

        child_counts = child_counts or {}
        return ManualJob(
            id=row["id"],
            scan_path=row["scan_path"],
            target_folder=row["target_folder"],
            metadata_dir=row["metadata_dir"] or "",
            scan_locator=scan_locator,
            target_locator=target_locator,
            metadata_locator=metadata_locator,
            allow_local_output=allow_local_output,
            link_mode=LinkMode(row["link_mode"]),
            delete_empty_parent=bool(row["delete_empty_parent"]),
            config_reuse_id=row["config_reuse_id"],
            source=JobSource(source_value),
            advanced_settings=advanced_settings,
            created_at=datetime.fromisoformat(row["created_at"]),
            started_at=datetime.fromisoformat(row["started_at"]) if row["started_at"] else None,
            finished_at=datetime.fromisoformat(row["finished_at"]) if row["finished_at"] else None,
            status=ManualJobStatus(row["status"]),
            success_count=row["success_count"],
            skip_count=row["skip_count"],
            error_count=row["error_count"],
            total_count=row["total_count"],
            error_message=row["error_message"],
            child_pending_count=child_counts.get("pending", 0),
            child_running_count=child_counts.get("running", 0),
            child_pending_action_count=child_counts.get("pending_action", 0),
        )


def _ensure_worker() -> None:
    """Ensure background worker is running."""
    global _worker_task
    if not _workers_stopping and (_worker_task is None or _worker_task.done()):
        _worker_task = asyncio.create_task(_job_worker())


async def _job_worker() -> None:
    """Background worker to process jobs from queue."""
    service = ManualJobService()

    while True:
        job_id = None
        execution_task = None
        try:
            job_id = await _job_queue.get()
            execution_task = asyncio.create_task(_execute_job(service, job_id))
            _active_job_tasks[job_id] = execution_task
            result = (await asyncio.gather(execution_task, return_exceptions=True))[0]
            if isinstance(result, Exception):
                raise result
        except asyncio.CancelledError:
            if execution_task is not None and not execution_task.done():
                execution_task.cancel()
                await asyncio.gather(execution_task, return_exceptions=True)
            break
        except Exception as e:
            logger.error(f"Job worker error: {e}")
        finally:
            if job_id is not None:
                _active_job_tasks.pop(job_id, None)
                _user_cancel_requests.discard(job_id)
                _job_queue.task_done()


async def _execute_job(service: ManualJobService, job_id: int) -> None:
    """Execute a single manual job - 扫描文件并投递到刮削任务队列"""
    from server.services.file_service import FileService
    from server.services.scrape_job_service import ScrapeJobService
    from server.models.scrape_job import ScrapeJobCreate, ScrapeJobSource
    from server.services.file_io import run_file_io

    if not await service.claim_job(job_id):
        logger.info(f"ManualJob {job_id} 已被其他 worker 领取或无需执行")
        return

    job = await service.get_job(job_id)
    if job is None:
        logger.error(f"Job {job_id} not found")
        return

    logger.info(f"Starting manual job {job_id}: {job.scan_path}")

    started_at = datetime.now()

    try:
        # 扫描文件
        file_service = FileService()
        scan_path = Path(job.scan_path)

        is_p115_source = (
            job.scan_locator is not None
            and job.scan_locator.provider == StorageProvider.P115
        )

        if is_p115_source:
            scan_result = await file_service.scan_folder_async(
                job.scan_locator.path or job.scan_path,
                locator=job.scan_locator,
            )
            files = [f.path for f in scan_result]
        elif await run_file_io(scan_path.is_file):
            files = [str(scan_path)]
        else:
            scan_result = await file_service.scan_folder_async(job.scan_path)
            files = [f.path for f in scan_result]

        total_count = len(files)
        await service.update_job_status(job_id, ManualJobStatus.RUNNING, total_count=total_count)

        if total_count == 0:
            await service.update_job_status(
                job_id,
                ManualJobStatus.SUCCESS,
                finished_at=datetime.now(),
                error_message="没有找到视频文件",
            )
            return

        # 为每个文件创建刮削任务
        scrape_service = ScrapeJobService()
        organize_mode = _link_mode_to_organize_mode(job.link_mode)
        dispatched_count = 0
        skipped_count = 0

        for file_path in files:
            logger.info(f"手动任务 #{job_id} 投递文件: {file_path}")
            # 115 源文件需要构造 file_locator，携带 file_id 以便刮削下载
            file_locator = None
            if is_p115_source:
                scanned = next((f for f in scan_result if f.path == file_path), None)
                if scanned is not None:
                    file_locator = _build_file_locator_from_scan(
                        job.scan_locator,
                        scanned,
                        file_path,
                    )
            job_create = ScrapeJobCreate(
                file_path=file_path,
                output_dir=job.target_folder,
                metadata_dir=job.metadata_dir or None,
                file_locator=file_locator,
                output_locator=job.target_locator,
                metadata_locator=job.metadata_locator,
                allow_local_output=job.allow_local_output,
                link_mode=organize_mode,
                source=ScrapeJobSource.MANUAL,
                source_id=job_id,
                advanced_settings=job.advanced_settings,
            )
            created = await scrape_service.create_job(job_create)
            if created is not None:
                dispatched_count += 1
            else:
                skipped_count += 1
                logger.info(f"手动任务 #{job_id} 跳过（已有处理中任务）: {file_path}")

        # 完成 - 手动任务只负责扫描和投递，不等待刮削完成
        await service.update_job_status(
            job_id,
            ManualJobStatus.SUCCESS,
            finished_at=datetime.now(),
            success_count=dispatched_count,  # 实际投递成功的数量
            skip_count=skipped_count,
        )
        logger.info(
            f"Manual job {job_id} completed: {dispatched_count} dispatched, {skipped_count} skipped"
        )

    except asyncio.CancelledError:
        # Shutdown keeps work pending for recovery; an explicit user request is terminal.
        if job_id in _user_cancel_requests:
            await service.update_job_status(
                job_id,
                ManualJobStatus.CANCELLED,
                finished_at=datetime.now(),
                error_message="用户已取消任务及尚未完成的刮削子任务",
            )
        else:
            await service.update_job_status(job_id, ManualJobStatus.PENDING)
        raise
    except Exception as e:
        logger.error(f"Manual job {job_id} failed: {e}")
        await service.update_job_status(
            job_id,
            ManualJobStatus.FAILED,
            finished_at=datetime.now(),
            error_message=str(e),
        )
        # 扫描阶段失败：创建一条失败历史记录，关联 manual_job_id，
        # 让用户在记录页能看到失败原因。
        try:
            from server.services.history_service import HistoryService
            from server.models.history import HistoryRecordCreate, TaskStatus, TaskSource, ConflictType
            duration = (datetime.now() - started_at).total_seconds()
            await HistoryService().create_record(HistoryRecordCreate(
                task_name=f"手动任务 #{job_id} 扫描失败",
                folder_path=job.scan_path,
                status=TaskStatus.FAILED,
                source=TaskSource.MANUAL,
                total_files=0,
                success_count=0,
                failed_count=1,
                duration_seconds=duration,
                manual_job_id=job_id,
                error_message=str(e),
                conflict_type=ConflictType.NO_MATCH,
                conflict_data={
                    "output_dir": job.target_folder,
                    "metadata_dir": job.metadata_dir or None,
                    "link_mode": _link_mode_to_organize_mode(job.link_mode).value,
                    "parsed_title": None,
                    "parsed_season": None,
                    "parsed_episode": None,
                },
            ))
        except Exception as hist_err:
            logger.warning(f"Failed to create history record for failed manual job {job_id}: {hist_err}")


async def shutdown_workers() -> None:
    """Cancel live work and reset in-memory state for a clean restart."""
    global _worker_task, _workers_stopping
    _workers_stopping = True
    try:
        worker = _worker_task
        _worker_task = None
        tasks = [
            task
            for task in [worker, *_active_job_tasks.values()]
            if task is not None
        ]
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        _worker_task = None
        _active_job_tasks.clear()
        _user_cancel_requests.clear()
        while True:
            try:
                _job_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            _job_queue.task_done()
        _workers_stopping = False
    logger.info("Manual job workers stopped and queue reset")


async def recover_pending_jobs() -> int:
    """Requeue persisted manual jobs after a process restart."""
    service = ManualJobService()
    job_ids = await service.prepare_recovery()
    for job_id in job_ids:
        await _job_queue.put(job_id)
    if job_ids:
        _ensure_worker()
        logger.info(f"Recovered {len(job_ids)} manual jobs from database")
    return len(job_ids)
