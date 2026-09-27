"""Scrape job service - 文件刮削任务服务"""

import asyncio
import json
import logging
import uuid
from datetime import datetime
from pathlib import Path
from weakref import WeakKeyDictionary

from server.common.path_security import PathSecurityError, validate_media_path
from server.infrastructure.db import DATABASE_PATH
from server.infrastructure.repositories.scrape_job_repository import ScrapeJobRepository
from server.models.manual_job import ManualJobAdvancedSettings
from server.models.scrape_job import (
    ScrapeJob,
    ScrapeJobCreate,
    ScrapeJobSource,
    ScrapeJobStatus,
)
from server.models.organize import OrganizeMode
from server.models.storage import (
    StorageLocator,
    StorageProvider,
    validate_locator_namespace,
)
from server.infrastructure.realtime import get_notifier
from server.domain.media.fingerprint_service import calculate_fingerprint
from server.application.file_io import run_file_io
from server.application.media_identity_service import MediaIdentityService

logger = logging.getLogger(__name__)

# 超时兜底步骤名：写进刮削日志时间轴的最后一步
TIMEOUT_STEP_NAME = "任务超时"


def resolve_timeout_step(logs: list) -> str | None:
    """超时前最后执行到哪一步：日志按执行顺序累积，最后一项就是它。

    步骤在开工前入列（如「搜索 TMDB」先 append 再发请求），所以末项是运行中或刚跑完的
    节点——两者无法从日志区分（末尾几步可能都已完成，超时卡在步骤之间的尾巴阶段，
    实测就有这种情况），因此文案只说「最后执行到」，不武断地说「卡在这一步」。
    已写入的超时兜底步骤本身不算节点。
    """
    for step in reversed(logs):
        if getattr(step, "name", None) != TIMEOUT_STEP_NAME:
            return step.name
    return None


def build_timeout_message(step_name: str | None, timeout_seconds: int) -> str:
    """超时原因：带上最后执行到的节点，列表页一行就能看出问题位置。"""
    if step_name:
        return f"任务超时（超过 {timeout_seconds} 秒，最后停在「{step_name}」节点）"
    return f"任务超时（超过 {timeout_seconds} 秒，尚未进入刮削步骤）"


def build_timeout_step(step_name: str | None, timeout_seconds: int, elapsed: float):
    """超时兜底步骤：把「停在哪、阈值多少、为什么不自动重试」写进时间轴。"""
    from server.models.history import ScrapeLogEntry, ScrapeLogLevel, ScrapeLogStep

    if step_name:
        first = ScrapeLogEntry(
            message=(
                f"超时节点: {step_name}"
                "（超时前最后执行到的步骤，其已产出的记录保留）"
            ),
            level=ScrapeLogLevel.ERROR,
        )
    else:
        first = ScrapeLogEntry(
            message="超时前尚未进入刮削步骤（卡在准备阶段）",
            level=ScrapeLogLevel.ERROR,
        )

    return ScrapeLogStep(
        name=TIMEOUT_STEP_NAME,
        completed=False,
        logs=[
            first,
            ScrapeLogEntry(
                message=(
                    f"超时阈值: {timeout_seconds} 秒"
                    "（系统设置 → 性能 → 任务超时，每次执行重新读取）"
                ),
                level=ScrapeLogLevel.WARNING,
            ),
            ScrapeLogEntry(
                message=(
                    f"本次已执行 {elapsed:.1f} 秒后中止；任务不会自动重试，"
                    "需要手动执行「重试刮削」"
                ),
                level=ScrapeLogLevel.WARNING,
            ),
        ],
    )


async def record_scrape_timeout(
    *,
    history_service,
    record_id: str,
    logs: list,
    step_name: str | None,
    timeout_seconds: int,
    elapsed: float,
) -> None:
    """超时现场的日志侧回写：追加一步超时兜底说明。

    刻意不改前面步骤的 completed：末步可能是真跑完了（超时卡在步骤之间的尾巴），
    改成「未完成」就是在说假话。卡住与否交给它自己的日志记录去说明。
    """
    logs.append(build_timeout_step(step_name, timeout_seconds, elapsed))
    await history_service.update_scrape_logs(record_id, logs)

# 任务队列和并发控制
_scrape_queue: asyncio.Queue[str] = asyncio.Queue()
_worker_tasks: list[asyncio.Task] = []
_semaphore: asyncio.Semaphore | None = None
_current_threads: int = 0
_initialization_task: asyncio.Task | None = None
_active_job_tasks: dict[str, asyncio.Task] = {}
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


def _discard_queued_job_ids(job_ids: set[str]) -> None:
    """Remove cancelled IDs while preserving the remaining queue order."""
    retained: list[str] = []
    while True:
        try:
            queued_id = _scrape_queue.get_nowait()
        except asyncio.QueueEmpty:
            break
        _scrape_queue.task_done()
        if queued_id not in job_ids:
            retained.append(queued_id)
    for queued_id in retained:
        _scrape_queue.put_nowait(queued_id)


def get_scrape_runtime_state(pending_count: int) -> dict[str, int]:
    """Return live scrape-worker state without opening another DB connection."""
    return {
        "queued_in_memory": min(_scrape_queue.qsize(), pending_count),
        "active_tasks": sum(not task.done() for task in _active_job_tasks.values()),
        "worker_count": sum(not task.done() for task in _worker_tasks),
        "concurrency_limit": _current_threads,
    }


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


class ScrapeJobService:
    """文件刮削任务服务"""

    def __init__(self, db_path: Path | None = None):
        """初始化服务"""
        self.db_path = db_path or DATABASE_PATH
        self._repo = ScrapeJobRepository(db_path)

    async def _ensure_db(self) -> None:
        """确保数据库目录存在并完成旧库列迁移。"""
        await self._repo.ensure_schema()

    async def get_pending_job_by_path(self, file_path: str) -> ScrapeJob | None:
        """根据文件路径获取仍在处理中的任务（pending/running/pending_action）。

        注意：不拦截 ``success`` 状态——用户应能重新整理已成功刮削的文件。
        """
        await self._ensure_db()

        row = await self._repo.get_pending_by_path(file_path)
        return self._row_to_job(row) if row else None

    async def get_pending_file_paths(self) -> set[str]:
        """获取所有待处理任务的文件路径"""
        await self._ensure_db()

        return await self._repo.get_pending_file_paths()

    async def create_job(self, job: ScrapeJobCreate, skip_duplicate_check: bool = False) -> ScrapeJob | None:
        """创建刮削任务并加入队列，如果已存在待处理任务则返回 None"""
        await self._ensure_db()

        for locator in (job.file_locator, job.output_locator, job.metadata_locator):
            if locator is None:
                continue
            try:
                validate_locator_namespace(locator)
            except ValueError as exc:
                raise PathSecurityError(str(exc)) from exc
        job = job.model_copy(
            update={
                "file_path": (
                    job.file_path
                    if job.file_locator and job.file_locator.provider == StorageProvider.P115
                    else str(validate_media_path(job.file_path))
                ),
                "output_dir": (
                    job.output_dir
                    if job.output_locator and job.output_locator.provider == StorageProvider.P115
                    else str(validate_media_path(job.output_dir))
                ),
                "metadata_dir": (
                    job.metadata_dir
                    if job.metadata_locator
                    and job.metadata_locator.provider == StorageProvider.P115
                    else (
                        str(validate_media_path(job.metadata_dir))
                        if job.metadata_dir
                        else None
                    )
                ),
            }
        )

        # 去重检查：如果已有待处理任务，跳过创建
        if not skip_duplicate_check:
            existing = await self.get_pending_job_by_path(job.file_path)
            if existing:
                logger.info(f"文件已有待处理任务，跳过: {job.file_path}")
                return None

            # Watcher deliveries are automatic.  Once a local source has a
            # recorded media version, a restart or repeated filesystem event
            # must not enqueue the same media again.  Explicit manual retries
            # still bypass this guard.
            if job.source == ScrapeJobSource.WATCHER:
                file_fingerprint = None
                if job.file_locator is None:
                    file_fingerprint = await run_file_io(
                        calculate_fingerprint, job.file_path
                    )
                if await self._repo.has_skipped_history(
                    job.file_path, file_fingerprint
                ):
                    logger.info(f"文件已有用户跳过记录，跳过监控重复任务: {job.file_path}")
                    return None
                if job.file_locator is None:
                    fingerprint = await run_file_io(
                        MediaIdentityService.fingerprint, job.file_path
                    )
                    if await self._repo.has_media_version(fingerprint):
                        logger.info(f"文件已有媒体版本记录，跳过监控重复任务: {job.file_path}")
                        return None

        job_id = str(uuid.uuid4())[:8]
        now = datetime.now()

        # 序列化高级设置
        advanced_settings_json = None
        if job.advanced_settings is not None:
            advanced_settings_json = json.dumps(job.advanced_settings.model_dump())
        file_locator_json = _serialize_locator(job.file_locator)
        output_locator_json = _serialize_locator(job.output_locator)
        metadata_locator_json = _serialize_locator(job.metadata_locator)

        inserted = await self._repo.insert_job(
            job_id,
            job,
            created_at=now.isoformat(),
            advanced_settings_json=advanced_settings_json,
            file_locator_json=file_locator_json,
            output_locator_json=output_locator_json,
            metadata_locator_json=metadata_locator_json,
            check_duplicate=not skip_duplicate_check,
        )
        if not inserted:
            logger.info(f"文件已有并发创建的待处理任务，跳过: {job.file_path}")
            return None

        created_job = ScrapeJob(
            id=job_id,
            file_path=job.file_path,
            output_dir=job.output_dir,
            metadata_dir=job.metadata_dir,
            file_locator=job.file_locator,
            output_locator=job.output_locator,
            metadata_locator=job.metadata_locator,
            allow_local_output=job.allow_local_output,
            link_mode=job.link_mode,
            source=job.source,
            source_id=job.source_id,
            advanced_settings=job.advanced_settings,
            replaces_job_id=job.replaces_job_id,
            correction_history_id=job.correction_history_id,
            correction_tmdb_id=job.correction_tmdb_id,
            correction_season=job.correction_season,
            correction_episode=job.correction_episode,
            continuation_history_id=job.continuation_history_id,
            file_action=job.file_action,
            selection_log=job.selection_log,
            skip_emby_check=job.skip_emby_check,
            status=ScrapeJobStatus.PENDING,
            created_at=now,
        )

        # 加入队列
        await _scrape_queue.put(job_id)
        # 确保 worker 在运行
        _ensure_worker()

        # 发送 WebSocket 通知
        notifier = get_notifier()
        await notifier.notify_job_created(job_id, job.file_path, ScrapeJobStatus.PENDING.value)

        return created_job

    async def create_replacement_job(
        self,
        job: ScrapeJob,
        *,
        replacement_history_id: str | None = None,
    ) -> ScrapeJob | None:
        """Queue a fresh job while retaining the original job for audit."""
        replacement = await self.create_job(
            ScrapeJobCreate(
                file_path=job.file_path,
                output_dir=job.output_dir,
                metadata_dir=job.metadata_dir,
                file_locator=job.file_locator,
                output_locator=job.output_locator,
                metadata_locator=job.metadata_locator,
                allow_local_output=job.allow_local_output,
                link_mode=job.link_mode,
                source=job.source,
                source_id=job.source_id,
                advanced_settings=job.advanced_settings,
                replaces_job_id=job.id,
                correction_history_id=replacement_history_id,
                correction_tmdb_id=job.correction_tmdb_id,
                correction_season=job.correction_season,
                correction_episode=job.correction_episode,
                continuation_history_id=job.continuation_history_id,
                file_action=job.file_action,
                selection_log=job.selection_log,
                skip_emby_check=job.skip_emby_check,
            ),
            skip_duplicate_check=True,
        )
        if replacement is not None:
            await self._repo.update_job(
                job.id,
                status=ScrapeJobStatus.REPLACED,
                finished_at=datetime.now(),
                replaced_by_job_id=replacement.id,
            )
        return replacement

    async def prepare_recovery(self) -> list[str]:
        """Reset interrupted work and return the durable pending queue."""
        await self._ensure_db()
        return await self._repo.prepare_recovery()

    async def claim_job(self, job_id: str) -> bool:
        """Atomically move one pending job to running."""
        async with _get_job_state_lock():
            await self._ensure_db()
            return await self._repo.claim_job(job_id)

    async def reset_job_to_pending(self, job_id: str) -> bool:
        """Return an interrupted running job to a clean pending state."""
        await self._ensure_db()
        return await self._repo.reset_job_to_pending(job_id)

    async def list_jobs(
        self,
        limit: int = 50,
        offset: int = 0,
        source: ScrapeJobSource | None = None,
        source_id: int | None = None,
        status: ScrapeJobStatus | None = None,
    ) -> tuple[list[ScrapeJob], int]:
        """列出刮削任务"""
        await self._ensure_db()

        rows, total = await self._repo.list_jobs(
            limit=limit, offset=offset, source=source, source_id=source_id, status=status
        )
        jobs = [self._row_to_job(row) for row in rows]
        return jobs, total

    async def get_job(self, job_id: str) -> ScrapeJob | None:
        """获取刮削任务"""
        await self._ensure_db()

        row = await self._repo.get_job_raw(job_id)
        if row is None:
            return None
        return self._row_to_job(row)

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
        """更新刮削任务"""
        await self._ensure_db()

        return await self._repo.update_job(
            job_id,
            status=status,
            started_at=started_at,
            finished_at=finished_at,
            error_message=error_message,
            clear_error_message=clear_error_message,
            history_record_id=history_record_id,
            replaced_by_job_id=replaced_by_job_id,
            expected_status=expected_status,
        )

    async def cancel_job(self, job_id: str) -> tuple[ScrapeJob | None, bool, str]:
        """Cancel queued/running/user-action work and finish its history."""
        message = "用户已取消任务；文件操作已停止或完成安全收尾"
        immediate_cancel = False
        task: asyncio.Task | None = None

        async with _get_job_state_lock():
            job = await self.get_job(job_id)
            if job is None:
                return None, False, "刮削任务不存在"

            cancellable = {
                ScrapeJobStatus.PENDING,
                ScrapeJobStatus.RUNNING,
                ScrapeJobStatus.PENDING_ACTION,
            }
            if job.status not in cancellable:
                return job, False, f"任务已经是 {job.status.value} 状态"

            task = _active_job_tasks.get(job_id)
            if job.status != ScrapeJobStatus.RUNNING or task is None or task.done():
                immediate_cancel = await self.update_job(
                    job_id,
                    status=ScrapeJobStatus.CANCELLED,
                    finished_at=datetime.now(),
                    error_message=message,
                    expected_status=job.status,
                )

        if not immediate_cancel and task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        updated = await self.get_job(job_id)
        if updated is not None and updated.status in cancellable:
            live_task = _active_job_tasks.get(job_id)
            if (
                updated.status == ScrapeJobStatus.RUNNING
                and live_task is not None
                and not live_task.done()
            ):
                live_task.cancel()
                await asyncio.gather(live_task, return_exceptions=True)
            else:
                # Covers a stale RUNNING row for which no live task exists,
                # and a worker cancelled before its terminal write completed.
                await self.update_job(
                    job_id,
                    status=ScrapeJobStatus.CANCELLED,
                    finished_at=datetime.now(),
                    error_message=message,
                    expected_status=updated.status,
                )
            updated = await self.get_job(job_id)

        if updated is not None and updated.status == ScrapeJobStatus.CANCELLED:
            history_id = updated.history_record_id or updated.continuation_history_id
            if history_id:
                from server.application.history_service import HistoryService
                from server.models.history import TaskStatus

                try:
                    await HistoryService(db_path=self.db_path).update_record(
                        history_id,
                        status=TaskStatus.CANCELLED,
                        error_message=message,
                    )
                except Exception:
                    logger.exception("Unable to finalize cancelled history: %s", history_id)
            try:
                await get_notifier().notify_cancelled(job_id, message)
            except Exception:
                logger.exception("Unable to notify cancelled scrape job")
            return updated, True, message

        return updated, False, f"任务已经是 {updated.status.value if updated else '未知'} 状态"

    async def cancel_jobs_by_source(self, source_id: int) -> int:
        """Cancel all unfinished scrape children dispatched by a manual job."""
        jobs, _ = await self.list_jobs(
            limit=500,
            offset=0,
            source=ScrapeJobSource.MANUAL,
            source_id=source_id,
        )
        active = [
            job for job in jobs
            if job.status in {
                ScrapeJobStatus.PENDING,
                ScrapeJobStatus.RUNNING,
                ScrapeJobStatus.PENDING_ACTION,
            }
        ]
        _discard_queued_job_ids({job.id for job in active})
        results = await asyncio.gather(
            *(self.cancel_job(job.id) for job in active),
            return_exceptions=True,
        )
        count = 0
        for result in results:
            if isinstance(result, tuple):
                count += int(result[1])
            elif isinstance(result, BaseException):
                logger.error("Unable to cancel scrape child: %r", result)
        return count

    async def delete_jobs(self, ids: list[str]) -> int:
        """删除刮削任务"""
        await self._ensure_db()

        return await self._repo.delete_jobs(ids)

    def _row_to_job(self, row) -> ScrapeJob:
        """转换数据库行到模型"""
        link_mode_value = row["link_mode"] if "link_mode" in row.keys() else None

        # 反序列化高级设置
        advanced_settings = None
        if "advanced_settings" in row.keys() and row["advanced_settings"]:
            try:
                settings_data = json.loads(row["advanced_settings"])
                advanced_settings = ManualJobAdvancedSettings(**settings_data)
            except (json.JSONDecodeError, ValueError):
                pass  # 解析失败则使用 None
        file_locator = _deserialize_locator(
            row["file_locator"] if "file_locator" in row.keys() else None
        )
        output_locator = _deserialize_locator(
            row["output_locator"] if "output_locator" in row.keys() else None
        )
        metadata_locator = _deserialize_locator(
            row["metadata_locator"] if "metadata_locator" in row.keys() else None
        )
        allow_local_output = bool(
            row["allow_local_output"] if "allow_local_output" in row.keys() else 0
        )

        return ScrapeJob(
            id=row["id"],
            file_path=row["file_path"],
            output_dir=row["output_dir"],
            metadata_dir=row["metadata_dir"],
            file_locator=file_locator,
            output_locator=output_locator,
            metadata_locator=metadata_locator,
            allow_local_output=allow_local_output,
            link_mode=OrganizeMode(link_mode_value) if link_mode_value else None,
            source=ScrapeJobSource(row["source"]),
            source_id=row["source_id"],
            advanced_settings=advanced_settings,
            status=ScrapeJobStatus(row["status"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            started_at=datetime.fromisoformat(row["started_at"]) if row["started_at"] else None,
            finished_at=datetime.fromisoformat(row["finished_at"]) if row["finished_at"] else None,
            error_message=row["error_message"],
            history_record_id=row["history_record_id"],
            replaces_job_id=row["replaces_job_id"] if "replaces_job_id" in row.keys() else None,
            replaced_by_job_id=row["replaced_by_job_id"] if "replaced_by_job_id" in row.keys() else None,
            correction_history_id=row["correction_history_id"] if "correction_history_id" in row.keys() else None,
            correction_tmdb_id=row["correction_tmdb_id"] if "correction_tmdb_id" in row.keys() else None,
            correction_season=row["correction_season"] if "correction_season" in row.keys() else None,
            correction_episode=row["correction_episode"] if "correction_episode" in row.keys() else None,
            continuation_history_id=row["continuation_history_id"] if "continuation_history_id" in row.keys() else None,
            file_action=row["file_action"] if "file_action" in row.keys() else None,
            selection_log=row["selection_log"] if "selection_log" in row.keys() else None,
            skip_emby_check=bool(row["skip_emby_check"]) if "skip_emby_check" in row.keys() else False,
        )


def _ensure_worker() -> None:
    """确保后台 worker 在运行，并根据配置调整并发数"""
    global _worker_tasks, _semaphore, _current_threads, _initialization_task

    if _workers_stopping:
        return

    async def _init_workers():
        global _semaphore, _current_threads, _worker_tasks, _initialization_task
        from server.domain.system.config_service import ConfigService

        try:
            config_service = ConfigService()
            system_config = await config_service.get_system_config()
            threads = system_config.scrape_threads

            if _workers_stopping:
                return

            # 如果并发数变化，重新初始化
            if threads != _current_threads:
                _current_threads = threads
                _semaphore = asyncio.Semaphore(threads)
                logger.info(f"刮削并发数设置为: {threads}")

            # 清理已完成的 worker
            _worker_tasks = [t for t in _worker_tasks if not t.done()]

            # 启动足够的 worker
            while len(_worker_tasks) < threads:
                task = asyncio.create_task(_scrape_worker())
                _worker_tasks.append(task)
        finally:
            if _initialization_task is asyncio.current_task():
                _initialization_task = None

    # 在事件循环中执行初始化
    try:
        loop = asyncio.get_running_loop()
        if _initialization_task is None or _initialization_task.done():
            _initialization_task = loop.create_task(_init_workers())
    except RuntimeError:
        pass


async def _scrape_worker() -> None:
    """后台 worker 处理刮削队列"""
    global _semaphore
    service = ScrapeJobService()

    while True:
        job_id: str | None = None
        execution_task: asyncio.Task | None = None
        try:
            job_id = await _scrape_queue.get()
            # 使用 Semaphore 控制并发
            if _semaphore:
                async with _semaphore:
                    execution_task = asyncio.create_task(
                        _execute_scrape_job(service, job_id)
                    )
                    _active_job_tasks[job_id] = execution_task
                    result = (await asyncio.gather(
                        execution_task, return_exceptions=True
                    ))[0]
            else:
                execution_task = asyncio.create_task(
                    _execute_scrape_job(service, job_id)
                )
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
            logger.error(f"Scrape worker error: {e}")
        finally:
            if job_id is not None:
                _active_job_tasks.pop(job_id, None)
                _scrape_queue.task_done()


async def _run_scrape_job(service: ScrapeJobService, job_id: str) -> None:
    """执行一个已经被原子领取的刮削任务。"""
    from server.bootstrap import get_scraper_service
    from server.application.history_service import HistoryService, build_record_folder_path
    from server.application.scraped_file_service import ScrapedFileService
    from server.domain.system.config_service import ConfigService
    from server.models.scraper import ScrapeByIdRequest, ScrapeRequest, ScrapeStatus
    from server.models.history import (
        HistoryRecordCreate,
        TaskStatus,
        HistoryConflictType,
        TaskSource,
        ScrapeLogEntry,
        ScrapeLogStep,
    )

    if not await service.claim_job(job_id):
        logger.info(f"ScrapeJob {job_id} 已被其他 worker 领取或无需执行")
        return

    job = await service.get_job(job_id)
    if job is None:
        logger.error(f"ScrapeJob {job_id} not found")
        return

    logger.info(f"Starting scrape job {job_id}: {job.file_path}")

    # 获取 WebSocket 通知器
    notifier = get_notifier()

    # 获取超时配置
    config_service = ConfigService()
    system_config = await config_service.get_system_config()
    timeout_seconds = system_config.task_timeout

    # claim_job 已经把 started_at 原子写入数据库；沿用它以保证前端计时一致。
    started_at = job.started_at or datetime.now()

    def elapsed_seconds() -> float:
        """本次执行已耗时：running 期间前端用 started_at 本地计时，落终态时写回真实值。"""
        return (datetime.now() - started_at).total_seconds()

    # 发送开始执行通知
    await notifier.notify_progress(job_id, "starting", 0, f"开始处理: {Path(job.file_path).name}")

    # 根据来源设置任务名称
    if job.source == ScrapeJobSource.WATCHER:
        task_name = f"文件刮削任务 #{job_id}"
        task_source = TaskSource.WATCHER
    else:
        task_name = f"文件刮削任务 #{job_id}"
        task_source = TaskSource.MANUAL

    history_service = HistoryService()
    scraped_file_service = ScrapedFileService()
    scraper = get_scraper_service()

    # 计算文件指纹
    file_fingerprint = calculate_fingerprint(job.file_path)

    # 冲突解决/重试沿用原历史记录，普通扫描才创建新记录。
    if job.continuation_history_id:
        history_record = await history_service.get_record(job.continuation_history_id)
        if history_record is None:
            raise RuntimeError("原历史记录不存在，无法继续处理")
        existing_logs = list(history_record.scrape_logs or [])
        if job.selection_log:
            existing_logs.append(
                ScrapeLogStep(
                    name="用户手动选择",
                    completed=True,
                    logs=[ScrapeLogEntry(message=job.selection_log)],
                )
            )
        await history_service.update_record(
            history_record.id,
            status=TaskStatus.RUNNING,
            error_message="",
            started_at=started_at,
            tmdb_id=job.correction_tmdb_id,
            season_number=job.correction_season,
            episode_number=job.correction_episode,
        )
        if existing_logs:
            await history_service.update_scrape_logs(history_record.id, existing_logs)
    else:
        existing_logs = []
        history_record = await history_service.create_record(HistoryRecordCreate(
            task_name=task_name,
            folder_path=job.file_path,
            status=TaskStatus.RUNNING,
            source=task_source,
            total_files=1,
            success_count=0,
            failed_count=0,
            duration_seconds=0,
            started_at=started_at,  # 供前端实时计时（executed_at 是记录创建时间，重跑时不等同于本次开始）
            scrape_job_id=job_id,
            # 关联手动任务 ID，便于按任务聚合查询历史记录
            manual_job_id=job.source_id if job.source == ScrapeJobSource.MANUAL else None,
            file_fingerprint=file_fingerprint,
        ))
    record_id = history_record.id

    # 更新任务关联的历史记录ID
    await service.update_job(job_id, history_record_id=record_id)

    # 创建日志回调（同时记住最后执行到的节点：超时要写清停在哪一步）
    current_logs: list = list(existing_logs)
    current_step_name: str | None = resolve_timeout_step(current_logs)

    async def on_log_update(logs):
        nonlocal current_logs, current_step_name
        if logs:
            current_logs = existing_logs + logs
            current_step_name = resolve_timeout_step(current_logs)
        await history_service.update_scrape_logs(record_id, current_logs)

    async def on_match_resolved(tmdb_id: int, season: int, episode: int):
        """匹配一确定就落库：这条记录超时/失败后仍能看出匹配的是哪部剧，重试直接沿用。"""
        await history_service.update_record(
            record_id,
            tmdb_id=tmdb_id,
            season_number=season,
            episode_number=episode,
        )

    try:
        if job.correction_tmdb_id is not None:
            request = ScrapeByIdRequest(
                file_path=job.file_path,
                tmdb_id=job.correction_tmdb_id,
                season=job.correction_season if job.correction_season is not None else 1,
                episode=job.correction_episode if job.correction_episode is not None else 1,
                output_dir=job.output_dir,
                metadata_dir=job.metadata_dir,
                file_locator=job.file_locator,
                output_locator=job.output_locator,
                metadata_locator=job.metadata_locator,
                allow_local_output=job.allow_local_output,
                link_mode=job.link_mode,
                skip_emby_check=job.skip_emby_check,
                file_action=job.file_action,
                advanced_settings=job.advanced_settings,
            )
            scrape_call = scraper.scrape_by_id(
                request,
                on_log_update=on_log_update,
            )
        else:
            request = ScrapeRequest(
                file_path=job.file_path,
                output_dir=job.output_dir,
                metadata_dir=job.metadata_dir,
                file_locator=job.file_locator,
                output_locator=job.output_locator,
                metadata_locator=job.metadata_locator,
                allow_local_output=job.allow_local_output,
                link_mode=job.link_mode,
                auto_select=True,
                skip_emby_check=job.skip_emby_check,
                file_action=job.file_action,
                advanced_settings=job.advanced_settings,
            )
            scrape_call = scraper.scrape_file(
                request,
                on_log_update=on_log_update,
                on_match_resolved=on_match_resolved,
            )
        # 使用超时控制
        result = await asyncio.wait_for(
            scrape_call,
            timeout=timeout_seconds,
        )
        file_duration = elapsed_seconds()

        if result.status == ScrapeStatus.SUCCESS:
            # 更新历史记录为成功
            series = result.series_info
            episode = result.episode_info
            await history_service.update_record_on_success(
                record_id,
                folder_path=build_record_folder_path(job.file_path, result.dest_path or job.output_dir),
                duration_seconds=file_duration,
                title=series.name if series else None,
                original_title=series.original_name if series else None,
                plot=series.overview if series else None,
                poster_url=f"https://image.tmdb.org/t/p/w500{series.poster_path}" if series and series.poster_path else None,
                release_date=str(series.first_air_date) if series and series.first_air_date else None,
                rating=series.vote_average if series else None,
                tags=series.genres if series else None,
                season_number=result.parsed_season,
                episode_number=result.parsed_episode,
                episode_title=episode.name if episode else None,
                episode_overview=episode.overview if episode else None,
                episode_still_url=f"https://image.tmdb.org/t/p/w500{episode.still_path}" if episode and episode.still_path else None,
                episode_air_date=str(episode.air_date) if episode and episode.air_date else None,
            )

            # 登记产物：scraped_files 是记录详情的「删除文件」与「重新整理」查找源文件的依据
            # （设置页的「已刮削文件」面板已于 2026-09-27 移除）。此前只有重刮/重新整理的收尾会登记，
            # 扫描流程刮削的记录一条都没有，于是这两个功能对这些记录全都没数据可看。
            # 登记失败不影响刮削结果，故单独兜住异常。
            try:
                await scraped_file_service.register_output(
                    history_record_id=record_id,
                    source_path=result.file_path or job.file_path,
                    target_path=result.dest_path,
                    tmdb_id=result.selected_id,
                    season=result.parsed_season,
                    episode=result.parsed_episode,
                    title=series.name if series else None,
                )
            except Exception as exc:  # noqa: BLE001 - 登记是附带动作，不能拖垮刮削结果
                logger.warning(f"登记刮削产物失败 {record_id}: {exc}")

            await service.update_job(
                job_id,
                status=ScrapeJobStatus.SUCCESS,
                finished_at=datetime.now(),
            )
            # 发送完成通知
            await notifier.notify_completed(job_id, {
                "status": "success",
                "file_path": job.file_path,
                "dest_path": result.dest_path,
            })
        elif result.status == ScrapeStatus.NEED_SELECTION:
            # 需要用户选择
            conflict_data = {
                "output_dir": job.output_dir,
                "metadata_dir": job.metadata_dir,
                "link_mode": job.link_mode.value if job.link_mode else None,
                "search_results": [r.model_dump(mode='json') for r in result.search_results] if result.search_results else [],
                "parsed_season": result.parsed_season,
                "parsed_episode": result.parsed_episode,
            }
            await history_service.update_record(
                record_id,
                status=TaskStatus.PENDING_ACTION,
                error_message=result.message,
                conflict_type=HistoryConflictType.NEED_SELECTION,
                conflict_data=conflict_data,
                duration_seconds=elapsed_seconds(),
            )
            await service.update_job(
                job_id,
                status=ScrapeJobStatus.PENDING_ACTION,
                finished_at=datetime.now(),
                error_message=result.message,
            )
            # 发送需要用户操作通知
            await notifier.notify_need_action(job_id, "need_selection", conflict_data)
        elif result.status == ScrapeStatus.NEED_SEASON_EPISODE:
            # 需要输入季集
            conflict_data = {
                "output_dir": job.output_dir,
                "metadata_dir": job.metadata_dir,
                "link_mode": job.link_mode.value if job.link_mode else None,
                "tmdb_id": result.selected_id,
                "series_info": result.series_info.model_dump(mode='json') if result.series_info else None,
            }
            await history_service.update_record(
                record_id,
                status=TaskStatus.PENDING_ACTION,
                error_message=result.message,
                conflict_type=HistoryConflictType.NEED_SEASON_EPISODE,
                conflict_data=conflict_data,
                duration_seconds=elapsed_seconds(),
            )
            await service.update_job(
                job_id,
                status=ScrapeJobStatus.PENDING_ACTION,
                finished_at=datetime.now(),
                error_message=result.message,
            )
            # 发送需要用户操作通知
            await notifier.notify_need_action(job_id, "need_season_episode", conflict_data)
        elif result.status == ScrapeStatus.FILE_CONFLICT:
            # 文件冲突
            conflict_data = {
                "output_dir": job.output_dir,
                "metadata_dir": job.metadata_dir,
                "link_mode": job.link_mode.value if job.link_mode else None,
                "tmdb_id": result.selected_id,
                "season": result.parsed_season,
                "episode": result.parsed_episode,
                "dest_path": result.dest_path,
            }
            await history_service.update_record(
                record_id,
                status=TaskStatus.PENDING_ACTION,
                error_message=result.message,
                conflict_type=HistoryConflictType.FILE_CONFLICT,
                conflict_data=conflict_data,
                duration_seconds=elapsed_seconds(),
            )
            await service.update_job(
                job_id,
                status=ScrapeJobStatus.PENDING_ACTION,
                finished_at=datetime.now(),
                error_message=result.message,
            )
            # 发送需要用户操作通知
            await notifier.notify_need_action(job_id, "file_conflict", conflict_data)
        elif result.status in (ScrapeStatus.NO_MATCH, ScrapeStatus.SEARCH_FAILED, ScrapeStatus.API_FAILED):
            # 需要手动输入 TMDB ID
            conflict_type_map = {
                ScrapeStatus.NO_MATCH: HistoryConflictType.NO_MATCH,
                ScrapeStatus.SEARCH_FAILED: HistoryConflictType.SEARCH_FAILED,
                ScrapeStatus.API_FAILED: HistoryConflictType.API_FAILED,
            }
            conflict_data = {
                "output_dir": job.output_dir,
                "metadata_dir": job.metadata_dir,
                "link_mode": job.link_mode.value if job.link_mode else None,
                "parsed_title": result.parsed_title,
                "parsed_season": result.parsed_season,
                "parsed_episode": result.parsed_episode,
            }
            await history_service.update_record(
                record_id,
                status=TaskStatus.PENDING_ACTION,
                error_message=result.message,
                conflict_type=conflict_type_map[result.status],
                conflict_data=conflict_data,
                duration_seconds=elapsed_seconds(),
            )
            await service.update_job(
                job_id,
                status=ScrapeJobStatus.PENDING_ACTION,
                finished_at=datetime.now(),
                error_message=result.message,
            )
            # 发送需要用户操作通知
            await notifier.notify_need_action(job_id, "need_tmdb_id", conflict_data)
        elif result.status == ScrapeStatus.EMBY_CONFLICT:
            # Emby 冲突
            conflict_data = {
                "output_dir": job.output_dir,
                "metadata_dir": job.metadata_dir,
                "link_mode": job.link_mode.value if job.link_mode else None,
                "tmdb_id": result.selected_id,
                "season": result.parsed_season,
                "episode": result.parsed_episode,
                "series_info": result.series_info.model_dump(mode='json') if result.series_info else None,
                "emby_message": result.message,
                "emby_conflict": result.emby_conflict.model_dump(mode='json') if result.emby_conflict else None,
            }
            await history_service.update_record(
                record_id,
                status=TaskStatus.PENDING_ACTION,
                error_message=result.message,
                conflict_type=HistoryConflictType.EMBY_CONFLICT,
                conflict_data=conflict_data,
                duration_seconds=elapsed_seconds(),
            )
            await service.update_job(
                job_id,
                status=ScrapeJobStatus.PENDING_ACTION,
                finished_at=datetime.now(),
                error_message=result.message,
            )
            # 发送需要用户操作通知
            await notifier.notify_need_action(job_id, "emby_conflict", conflict_data)
        else:
            # 其他失败
            await history_service.update_record(
                record_id,
                status=TaskStatus.FAILED,
                error_message=result.message,
                duration_seconds=elapsed_seconds(),
            )
            await service.update_job(
                job_id,
                status=ScrapeJobStatus.FAILED,
                finished_at=datetime.now(),
                error_message=result.message,
            )
            # 发送失败通知
            await notifier.notify_failed(job_id, result.message or "未知错误")

        # 清理日志缓存
        history_service.clear_log_cache(record_id)

    except asyncio.TimeoutError:
        timeout_msg = build_timeout_message(current_step_name, timeout_seconds)
        logger.warning(
            f"ScrapeJob {job_id} timeout after {timeout_seconds}s at step "
            f"{current_step_name or 'unknown'}: {job.file_path}"
        )
        # 先补上超时现场（被中断的步骤 + 兜底步骤），再写终态字段
        try:
            await record_scrape_timeout(
                history_service=history_service,
                record_id=record_id,
                logs=current_logs,
                step_name=current_step_name,
                timeout_seconds=timeout_seconds,
                elapsed=elapsed_seconds(),
            )
        except Exception as log_error:
            # 日志回写失败不能让超时状态丢失
            logger.error(f"ScrapeJob {job_id} timeout log write failed: {log_error}")
        await history_service.update_record(
            record_id,
            status=TaskStatus.TIMEOUT,
            error_message=timeout_msg,
            duration_seconds=elapsed_seconds(),
            timeout_step=current_step_name,
            timeout_seconds=timeout_seconds,
        )
        await service.update_job(
            job_id,
            status=ScrapeJobStatus.TIMEOUT,
            finished_at=datetime.now(),
            error_message=timeout_msg,
        )
        # 发送失败通知
        await notifier.notify_failed(job_id, timeout_msg)
        await history_service.flush_and_clear_log_cache(record_id)

    except Exception as e:
        error_msg = str(e) or repr(e) or type(e).__name__
        logger.error(f"Error scraping {job.file_path}: {error_msg}")
        await history_service.update_record(
            record_id,
            status=TaskStatus.FAILED,
            error_message=error_msg,
            duration_seconds=elapsed_seconds(),
        )
        await service.update_job(
            job_id,
            status=ScrapeJobStatus.FAILED,
            finished_at=datetime.now(),
            error_message=error_msg,
        )
        # 发送失败通知
        await notifier.notify_failed(job_id, error_msg)
        history_service.clear_log_cache(record_id)

    logger.info(f"ScrapeJob {job_id} completed with status: {job.status}")


async def _execute_scrape_job(service: ScrapeJobService, job_id: str) -> None:
    """Finalize cancellation and pre-finalization failures.

    The scraper body has its own result/timeout state machine.  This outer
    guard covers cancellation during configuration, fingerprinting, history
    creation, or any other preparation step that previously could strand a
    claimed row in ``running`` forever.
    """
    try:
        await _run_scrape_job(service, job_id)
    except asyncio.CancelledError:
        job = await service.get_job(job_id)
        if job is None:
            raise

        history_id = job.history_record_id or job.continuation_history_id
        from server.application.history_service import HistoryService
        from server.models.history import TaskStatus

        history_service = HistoryService(db_path=service.db_path)
        if _workers_stopping:
            # Shutdown is recoverable: leave the durable job pending.  The
            # history boundary is marked failed so the next attempt creates a
            # fresh running record instead of showing a permanently running
            # entry from the previous process.
            if history_id:
                try:
                    await history_service.update_record(
                        history_id,
                        status=TaskStatus.FAILED,
                        error_message="任务因服务重启中断，已重新排队",
                    )
                except Exception:
                    logger.exception("Unable to finalize recovered history: %s", history_id)
            await service.reset_job_to_pending(job_id)
            raise

        message = "任务已取消，文件操作已停止或完成安全收尾；请核对日志和目标文件"
        transitioned = await service.update_job(
            job_id,
            status=ScrapeJobStatus.CANCELLED,
            finished_at=datetime.now(),
            error_message=message,
            expected_status=ScrapeJobStatus.RUNNING,
        )
        if transitioned and history_id:
            try:
                await history_service.update_record(
                    history_id,
                    status=TaskStatus.CANCELLED,
                    error_message=message,
                )
                await history_service.flush_and_clear_log_cache(history_id)
            except Exception:
                logger.exception("Unable to finalize cancelled history: %s", history_id)
        if transitioned:
            try:
                await get_notifier().notify_cancelled(job_id, message)
            except Exception:
                logger.exception("Unable to notify cancelled scrape job: %s", job_id)
        raise
    except Exception as exc:
        error = str(exc) or repr(exc) or type(exc).__name__
        message = f"任务执行异常: {error}"
        logger.exception("Unhandled scrape job failure before finalization: %s", job_id)
        job = await service.get_job(job_id)
        if job is None or job.status != ScrapeJobStatus.RUNNING:
            return

        history_id = job.history_record_id or job.continuation_history_id
        transitioned = await service.update_job(
            job_id,
            status=ScrapeJobStatus.FAILED,
            finished_at=datetime.now(),
            error_message=message,
            expected_status=ScrapeJobStatus.RUNNING,
        )
        if history_id:
            from server.application.history_service import HistoryService
            from server.models.history import TaskStatus

            history_service = HistoryService(db_path=service.db_path)
            try:
                await history_service.update_record(
                    history_id,
                    status=TaskStatus.FAILED,
                    error_message=message,
                )
                await history_service.flush_and_clear_log_cache(history_id)
            except Exception:
                logger.exception("Unable to finalize failed history: %s", history_id)
        if transitioned:
            try:
                await get_notifier().notify_failed(job_id, message)
            except Exception:
                logger.exception("Unable to notify failed scrape job: %s", job_id)


async def shutdown_workers() -> None:
    """Stop workers and leave interrupted durable work recoverable."""
    global _worker_tasks, _initialization_task, _semaphore, _current_threads
    global _workers_stopping

    _workers_stopping = True
    try:
        tasks = [
            task
            for task in [
                _initialization_task,
                *_worker_tasks,
                *_active_job_tasks.values(),
            ]
            if task is not None
        ]
        # A task can be present in both the worker list and active map.
        tasks = list(dict.fromkeys(tasks))
        _initialization_task = None
        _worker_tasks = []
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        _initialization_task = None
        _worker_tasks = []
        _active_job_tasks.clear()
        while True:
            try:
                _scrape_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            _scrape_queue.task_done()
        _semaphore = None
        _current_threads = 0
        _workers_stopping = False
    logger.info("Scrape workers stopped and queue reset")


async def recover_pending_jobs() -> int:
    """Requeue persisted scrape jobs after startup or an unclean stop."""
    service = ScrapeJobService()
    job_ids = await service.prepare_recovery()
    for job_id in job_ids:
        await _scrape_queue.put(job_id)
    if job_ids:
        _ensure_worker()
        logger.info("Recovered %s scrape jobs from database", len(job_ids))
    return len(job_ids)
