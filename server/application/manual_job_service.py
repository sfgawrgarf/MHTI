"""Manual job service for managing manual scrape tasks."""

import asyncio
import json
import logging
from server.application.file_io import run_file_io
from datetime import datetime
from pathlib import Path
from weakref import WeakKeyDictionary

from server.common.path_security import PathSecurityError, validate_media_path
from server.infrastructure.db import DATABASE_PATH
from server.infrastructure.repositories.manual_job_repository import ManualJobRepository
from server.models.manual_job import (
    JobSource,
    LinkMode,
    ManualJob,
    ManualJobAdvancedSettings,
    ManualJobCreate,
    ManualJobStatus,
)
from server.models.organize import OrganizeMode
from server.models.storage import (
    StorageLocator,
    StorageProvider,
    validate_locator_namespace,
)

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
_job_state_locks: WeakKeyDictionary = WeakKeyDictionary()
_user_cancel_requests: set[int] = set()
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
    """Return live manual-worker state without opening another DB connection."""
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
        self._repo = ManualJobRepository(db_path)

    async def _ensure_db(self) -> None:
        """确保数据库目录存在并完成旧库列迁移。"""
        await self._repo.ensure_schema()

    async def create_job(self, job: ManualJobCreate) -> ManualJob:
        """Create a new manual job and add to queue."""
        await self._ensure_db()

        locators = (job.scan_locator, job.target_locator, job.metadata_locator)
        for locator in locators:
            if locator is None:
                continue
            try:
                validate_locator_namespace(locator)
            except ValueError as exc:
                raise PathSecurityError(str(exc)) from exc
        job = job.model_copy(
            update={
                "scan_path": (
                    job.scan_path
                    if job.scan_locator and job.scan_locator.provider == StorageProvider.P115
                    else str(validate_media_path(job.scan_path))
                ),
                "target_folder": (
                    job.target_folder
                    if job.target_locator and job.target_locator.provider == StorageProvider.P115
                    else str(validate_media_path(job.target_folder))
                ),
                "metadata_dir": (
                    job.metadata_dir
                    if job.metadata_locator
                    and job.metadata_locator.provider == StorageProvider.P115
                    else (
                        str(validate_media_path(job.metadata_dir))
                        if job.metadata_dir
                        else ""
                    )
                ),
            }
        )
        now = datetime.now()

        # 序列化高级设置
        advanced_settings_json = None
        if job.advanced_settings is not None:
            advanced_settings_json = json.dumps(job.advanced_settings.model_dump())
        scan_locator_json = _serialize_locator(job.scan_locator)
        target_locator_json = _serialize_locator(job.target_locator)
        metadata_locator_json = _serialize_locator(job.metadata_locator)

        job_id = await self._repo.insert_job(
            job,
            created_at=now.isoformat(),
            advanced_settings_json=advanced_settings_json,
            scan_locator_json=scan_locator_json,
            target_locator_json=target_locator_json,
            metadata_locator_json=metadata_locator_json,
        )

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
        """Reset interrupted manual scans and return the durable queue."""
        await self._ensure_db()
        return await self._repo.prepare_recovery()

    async def claim_job(self, job_id: int) -> bool:
        """Atomically move one pending manual job to running."""
        async with _get_job_state_lock():
            await self._ensure_db()
            return await self._repo.claim_job(job_id)

    async def list_jobs(
        self,
        limit: int = 20,
        offset: int = 0,
        search: str | None = None,
        status: ManualJobStatus | None = None,
    ) -> tuple[list[ManualJob], int]:
        """List manual jobs with pagination, search and filter."""
        await self._ensure_db()

        rows, total = await self._repo.list_jobs(
            limit=limit, offset=offset, search=search, status=status
        )
        child_counts = await self._repo.get_child_counts([int(row["id"]) for row in rows])
        jobs = [
            self._row_to_job(row, child_counts.get(int(row["id"])))
            for row in rows
        ]
        return jobs, total

    async def get_job(self, job_id: int) -> ManualJob | None:
        """Get a manual job by ID."""
        await self._ensure_db()

        row = await self._repo.get_job_raw(job_id)
        if row is None:
            return None
        child_counts = await self._repo.get_child_counts([job_id])
        return self._row_to_job(row, child_counts.get(job_id))

    async def delete_jobs(self, ids: list[int]) -> int:
        """Delete manual jobs by IDs.

        同时级联删除关联的刮削记录（history_records.manual_job_id）。
        """
        await self._ensure_db()

        return await self._repo.delete_jobs(ids)

    async def cancel_job(
        self, job_id: int
    ) -> tuple[ManualJob | None, bool, int, str]:
        """Cancel a scan and all unfinished scrape children it dispatched."""
        from server.application.scrape_job_service import ScrapeJobService

        scrape_service = ScrapeJobService(db_path=self.db_path)
        message = "用户已取消任务及尚未完成的刮削子任务"
        task: asyncio.Task | None = None

        async with _get_job_state_lock():
            job = await self.get_job(job_id)
            if job is None:
                return None, False, 0, "手动任务不存在"
            manual_active = job.status in {
                ManualJobStatus.PENDING,
                ManualJobStatus.RUNNING,
            }
            task = _active_job_tasks.get(job_id)
            if job.status == ManualJobStatus.RUNNING and task is not None and not task.done():
                _user_cancel_requests.add(job_id)
            elif manual_active:
                await self.update_job_status(
                    job_id,
                    ManualJobStatus.CANCELLED,
                    finished_at=datetime.now(),
                    error_message=message,
                    expected_status=job.status,
                )

        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        cancelled_children = await scrape_service.cancel_jobs_by_source(job_id)
        if not manual_active and cancelled_children == 0:
            return job, False, 0, f"任务已经是 {job.status.value} 状态且没有运行中的子任务"

        updated = await self.get_job(job_id)
        if updated is not None and updated.status != ManualJobStatus.CANCELLED:
            live_task = _active_job_tasks.get(job_id)
            if (
                updated.status == ManualJobStatus.RUNNING
                and live_task is not None
                and not live_task.done()
            ):
                _user_cancel_requests.add(job_id)
                live_task.cancel()
                await asyncio.gather(live_task, return_exceptions=True)
            else:
                await self.update_job_status(
                    job_id,
                    ManualJobStatus.CANCELLED,
                    finished_at=datetime.now(),
                    error_message=message,
                    expected_status=updated.status,
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
        expected_status: ManualJobStatus | None = None,
    ) -> bool:
        """Update job status and counts."""
        await self._ensure_db()

        return await self._repo.update_job_status(
            job_id,
            status,
            started_at=started_at,
            finished_at=finished_at,
            success_count=success_count,
            skip_count=skip_count,
            error_count=error_count,
            total_count=total_count,
            error_message=error_message,
            expected_status=expected_status,
        )

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
        job_id: int | None = None
        execution_task: asyncio.Task | None = None
        try:
            job_id = await _job_queue.get()
            execution_task = asyncio.create_task(_execute_job(service, job_id))
            _active_job_tasks[job_id] = execution_task
            result = (await asyncio.gather(
                execution_task, return_exceptions=True
            ))[0]
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


async def _run_manual_job(service: ManualJobService, job_id: int) -> None:
    """Execute a single manual job - 扫描文件并投递到刮削任务队列"""
    from server.application.organize_filters import OrganizeFilter
    from server.domain.media.file_service import FileService
    from server.domain.system.config_service import ConfigService, ORGANIZE_CONFIG_KEY
    from server.application.scrape_job_service import ScrapeJobService
    from server.models.file import ScannedFile
    from server.models.scrape_job import ScrapeJobCreate, ScrapeJobSource

    if not await service.claim_job(job_id):
        logger.info(f"ManualJob {job_id} 已被其他 worker 领取或无需执行")
        return

    job = await service.get_job(job_id)
    if job is None:
        logger.error(f"Job {job_id} not found")
        return

    logger.info(f"Starting manual job {job_id}: {job.scan_path}")

    started_at = job.started_at or datetime.now()

    try:
        # 扫描文件
        file_service = FileService()
        is_p115_source = (
            job.scan_locator is not None
            and job.scan_locator.provider == StorageProvider.P115
        )
        if not is_p115_source:
            scan_path = validate_media_path(job.scan_path)
        else:
            scan_path = Path(job.scan_path)

        organize_filter = None
        if job.advanced_settings is not None and not job.advanced_settings.use_global_organize:
            organize_filter = OrganizeFilter.from_task_settings(job.advanced_settings)
        else:
            config_service = ConfigService(db_path=service.db_path)
            if await config_service.exists(ORGANIZE_CONFIG_KEY):
                organize_filter = OrganizeFilter.from_global_config(
                    await config_service.get_organize_config()
                )

        if is_p115_source:
            scan_result = await file_service.scan_folder_async(
                job.scan_locator.path or job.scan_path,
                locator=job.scan_locator,
            )
        elif scan_path.is_file():
            stat = scan_path.stat()
            scan_result = [
                ScannedFile(
                    filename=scan_path.name,
                    path=str(scan_path),
                    size=stat.st_size,
                    extension=scan_path.suffix.lower(),
                )
            ]
        else:
            scan_result = await run_file_io(file_service.scan_folder, job.scan_path)

        discovered_count = len(scan_result)
        if organize_filter is not None:
            scan_result = [
                scanned
                for scanned in scan_result
                if organize_filter.allows(scanned.filename, scanned.size)
            ]
            if len(scan_result) != discovered_count:
                logger.info(
                    "手动任务 #%s 按整理过滤跳过 %s 个文件",
                    job_id,
                    discovered_count - len(scan_result),
                )
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
        child_settings = (
            job.advanced_settings or ManualJobAdvancedSettings()
        ).model_copy(update={"delete_empty_parent": job.delete_empty_parent})
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
                advanced_settings=child_settings,
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
        if job_id in _user_cancel_requests:
            await service.update_job_status(
                job_id,
                ManualJobStatus.CANCELLED,
                finished_at=datetime.now(),
                error_message="用户已取消任务及尚未完成的刮削子任务",
            )
        elif _workers_stopping:
            await service.update_job_status(job_id, ManualJobStatus.PENDING)
        else:
            await service.update_job_status(
                job_id,
                ManualJobStatus.CANCELLED,
                finished_at=datetime.now(),
                error_message="任务已取消",
            )
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
            from server.application.history_service import HistoryService
            from server.models.history import HistoryRecordCreate, TaskStatus, TaskSource, HistoryConflictType
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
                conflict_type=HistoryConflictType.NO_MATCH,
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


async def _execute_job(service: ManualJobService, job_id: int) -> None:
    """Close lifecycle gaps before the manual scan enters its main try block."""
    try:
        await _run_manual_job(service, job_id)
    except asyncio.CancelledError:
        job = await service.get_job(job_id)
        if job is not None and job.status == ManualJobStatus.RUNNING:
            if _workers_stopping:
                await service.update_job_status(job_id, ManualJobStatus.PENDING)
            else:
                await service.update_job_status(
                    job_id,
                    ManualJobStatus.CANCELLED,
                    finished_at=datetime.now(),
                    error_message="任务已取消",
                )
        raise
    except Exception as exc:
        logger.exception("Unhandled manual job failure before finalization: %s", job_id)
        job = await service.get_job(job_id)
        if job is not None and job.status == ManualJobStatus.RUNNING:
            await service.update_job_status(
                job_id,
                ManualJobStatus.FAILED,
                finished_at=datetime.now(),
                error_message=str(exc) or repr(exc),
            )


async def shutdown_workers() -> None:
    """Stop the worker and leave interrupted scans recoverable."""
    global _worker_task, _workers_stopping
    _workers_stopping = True
    try:
        worker = _worker_task
        _worker_task = None
        tasks = [
            task for task in [worker, *_active_job_tasks.values()]
            if task is not None
        ]
        tasks = list(dict.fromkeys(tasks))
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
    """Requeue persisted manual jobs after startup or an unclean stop."""
    service = ManualJobService()
    job_ids = await service.prepare_recovery()
    for job_id in job_ids:
        await _job_queue.put(job_id)
    if job_ids:
        _ensure_worker()
        logger.info("Recovered %s manual jobs from database", len(job_ids))
    return len(job_ids)
