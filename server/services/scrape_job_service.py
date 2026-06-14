"""Scrape job service - 文件刮削任务服务"""

import asyncio
import json
import logging
import uuid
from datetime import datetime
from pathlib import Path

import aiosqlite

from server.core.database import DATABASE_PATH, _configure_connection
from server.models.manual_job import ManualJobAdvancedSettings
from server.models.scrape_job import (
    ScrapeJob,
    ScrapeJobCreate,
    ScrapeJobSource,
    ScrapeJobStatus,
)
from server.models.organize import OrganizeMode
from server.models.storage import StorageLocator
from server.services.websocket_manager import get_notifier
from server.services.fingerprint_service import calculate_fingerprint

logger = logging.getLogger(__name__)

# 任务队列和并发控制
_scrape_queue: asyncio.Queue[str] = asyncio.Queue()
_worker_tasks: list[asyncio.Task] = []
_semaphore: asyncio.Semaphore | None = None
_current_threads: int = 0


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

    async def _ensure_db(self) -> None:
        """确保数据库目录存在并运行迁移"""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.db_path) as db:
            await _configure_connection(db)
            # 迁移：为旧表添加 link_mode 列
            try:
                await db.execute("ALTER TABLE scrape_jobs ADD COLUMN link_mode TEXT")
            except Exception:
                pass  # 列已存在
            # 迁移：为旧表添加 advanced_settings 列
            try:
                await db.execute("ALTER TABLE scrape_jobs ADD COLUMN advanced_settings TEXT")
            except Exception:
                pass  # 列已存在
            try:
                await db.execute("ALTER TABLE scrape_jobs ADD COLUMN file_locator TEXT")
            except Exception:
                pass  # 列已存在
            try:
                await db.execute("ALTER TABLE scrape_jobs ADD COLUMN output_locator TEXT")
            except Exception:
                pass  # 列已存在
            try:
                await db.execute("ALTER TABLE scrape_jobs ADD COLUMN metadata_locator TEXT")
            except Exception:
                pass  # 列已存在
            try:
                await db.execute("ALTER TABLE scrape_jobs ADD COLUMN allow_local_output INTEGER DEFAULT 0")
            except Exception:
                pass  # 列已存在
            await db.commit()

    async def get_pending_job_by_path(self, file_path: str) -> ScrapeJob | None:
        """根据文件路径获取仍在处理中的任务（pending/running/pending_action）。

        注意：不拦截 ``success`` 状态——用户应能重新整理已成功刮削的文件。
        """
        await self._ensure_db()

        async with aiosqlite.connect(self.db_path) as db:
            await _configure_connection(db)
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """SELECT * FROM scrape_jobs
                WHERE file_path = ? AND status IN ('pending', 'running', 'pending_action')
                ORDER BY created_at DESC LIMIT 1""",
                (file_path,),
            )
            row = await cursor.fetchone()

        return self._row_to_job(row) if row else None

    async def get_pending_file_paths(self) -> set[str]:
        """获取所有待处理任务的文件路径"""
        await self._ensure_db()

        async with aiosqlite.connect(self.db_path) as db:
            await _configure_connection(db)
            cursor = await db.execute(
                "SELECT file_path FROM scrape_jobs WHERE status IN ('pending', 'running', 'pending_action')"
            )
            rows = await cursor.fetchall()

        return {row[0] for row in rows}

    async def create_job(self, job: ScrapeJobCreate, skip_duplicate_check: bool = False) -> ScrapeJob | None:
        """创建刮削任务并加入队列，如果已存在待处理任务则返回 None"""
        await self._ensure_db()

        # 去重检查：如果已有待处理任务，跳过创建
        if not skip_duplicate_check:
            existing = await self.get_pending_job_by_path(job.file_path)
            if existing:
                logger.info(f"文件已有待处理任务，跳过: {job.file_path}")
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

        async with aiosqlite.connect(self.db_path) as db:
            await _configure_connection(db)
            await db.execute(
                """
                INSERT INTO scrape_jobs
                (id, file_path, output_dir, metadata_dir, link_mode, source, source_id,
                 advanced_settings, file_locator, output_locator, metadata_locator,
                 allow_local_output, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    job.file_path,
                    job.output_dir,
                    job.metadata_dir,
                    job.link_mode.value if job.link_mode else None,
                    job.source.value,
                    job.source_id,
                    advanced_settings_json,
                    file_locator_json,
                    output_locator_json,
                    metadata_locator_json,
                    1 if job.allow_local_output else 0,
                    ScrapeJobStatus.PENDING.value,
                    now.isoformat(),
                ),
            )
            await db.commit()

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

        async with aiosqlite.connect(self.db_path) as db:
            await _configure_connection(db)
            db.row_factory = aiosqlite.Row

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

        jobs = [self._row_to_job(row) for row in rows]
        return jobs, total

    async def get_job(self, job_id: str) -> ScrapeJob | None:
        """获取刮削任务"""
        await self._ensure_db()

        async with aiosqlite.connect(self.db_path) as db:
            await _configure_connection(db)
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM scrape_jobs WHERE id = ?",
                (job_id,),
            )
            row = await cursor.fetchone()

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
        history_record_id: str | None = None,
    ) -> None:
        """更新刮削任务"""
        await self._ensure_db()

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
        if error_message is not None:
            updates.append("error_message = ?")
            params.append(error_message)
        if history_record_id is not None:
            updates.append("history_record_id = ?")
            params.append(history_record_id)

        if not updates:
            return

        params.append(job_id)

        async with aiosqlite.connect(self.db_path) as db:
            await _configure_connection(db)
            await db.execute(
                f"UPDATE scrape_jobs SET {', '.join(updates)} WHERE id = ?",
                params,
            )
            await db.commit()

    async def delete_jobs(self, ids: list[str]) -> int:
        """删除刮削任务"""
        await self._ensure_db()

        if not ids:
            return 0

        placeholders = ",".join("?" * len(ids))
        async with aiosqlite.connect(self.db_path) as db:
            await _configure_connection(db)
            cursor = await db.execute(
                f"DELETE FROM scrape_jobs WHERE id IN ({placeholders})",
                ids,
            )
            await db.commit()
            return cursor.rowcount

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
        )


def _ensure_worker() -> None:
    """确保后台 worker 在运行，并根据配置调整并发数"""
    global _worker_tasks, _semaphore, _current_threads

    async def _init_workers():
        global _semaphore, _current_threads, _worker_tasks
        from server.services.config_service import ConfigService

        config_service = ConfigService()
        system_config = await config_service.get_system_config()
        threads = system_config.scrape_threads

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

    # 在事件循环中执行初始化
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_init_workers())
    except RuntimeError:
        pass


async def _scrape_worker() -> None:
    """后台 worker 处理刮削队列"""
    global _semaphore
    service = ScrapeJobService()

    while True:
        try:
            job_id = await _scrape_queue.get()
            # 使用 Semaphore 控制并发
            if _semaphore:
                async with _semaphore:
                    await _execute_scrape_job(service, job_id)
            else:
                await _execute_scrape_job(service, job_id)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Scrape worker error: {e}")


async def _execute_scrape_job(service: ScrapeJobService, job_id: str) -> None:
    """执行单个刮削任务"""
    from server.core.container import get_scraper_service
    from server.services.history_service import HistoryService
    from server.services.config_service import ConfigService
    from server.models.scraper import ScrapeRequest, ScrapeStatus
    from server.models.history import HistoryRecordCreate, TaskStatus, ConflictType, TaskSource

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

    # 更新状态为运行中
    started_at = datetime.now()
    await service.update_job(job_id, status=ScrapeJobStatus.RUNNING, started_at=started_at)

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
    scraper = get_scraper_service()

    # 计算文件指纹
    file_fingerprint = calculate_fingerprint(job.file_path)

    # 创建历史记录
    history_record = await history_service.create_record(HistoryRecordCreate(
        task_name=task_name,
        folder_path=job.file_path,
        status=TaskStatus.RUNNING,
        source=task_source,
        total_files=1,
        success_count=0,
        failed_count=0,
        duration_seconds=0,
        scrape_job_id=job_id,
        # 关联手动任务 ID，便于按任务聚合查询历史记录
        manual_job_id=job.source_id if job.source == ScrapeJobSource.MANUAL else None,
        file_fingerprint=file_fingerprint,
    ))
    record_id = history_record.id

    # 更新任务关联的历史记录ID
    await service.update_job(job_id, history_record_id=record_id)

    # 创建日志回调
    async def on_log_update(logs):
        await history_service.update_scrape_logs(record_id, logs)

    try:
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
            advanced_settings=job.advanced_settings,
        )
        # 使用超时控制
        result = await asyncio.wait_for(
            scraper.scrape_file(request, on_log_update=on_log_update),
            timeout=timeout_seconds,
        )
        file_duration = (datetime.now() - started_at).total_seconds()

        if result.status == ScrapeStatus.SUCCESS:
            # 更新历史记录为成功
            series = result.series_info
            episode = result.episode_info
            await history_service.update_record_on_success(
                record_id,
                folder_path=f"{job.file_path} => {result.dest_path or job.output_dir}",
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
                conflict_type=ConflictType.NEED_SELECTION,
                conflict_data=conflict_data,
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
                conflict_type=ConflictType.NEED_SEASON_EPISODE,
                conflict_data=conflict_data,
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
                conflict_type=ConflictType.FILE_CONFLICT,
                conflict_data=conflict_data,
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
                ScrapeStatus.NO_MATCH: ConflictType.NO_MATCH,
                ScrapeStatus.SEARCH_FAILED: ConflictType.SEARCH_FAILED,
                ScrapeStatus.API_FAILED: ConflictType.API_FAILED,
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
                conflict_type=ConflictType.EMBY_CONFLICT,
                conflict_data=conflict_data,
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
        timeout_msg = f"任务超时（超过 {timeout_seconds} 秒）"
        logger.warning(f"ScrapeJob {job_id} timeout: {job.file_path}")
        await history_service.update_record(
            record_id,
            status=TaskStatus.TIMEOUT,
            error_message=timeout_msg,
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


async def shutdown_workers() -> None:
    """取消所有刮削 worker 任务，避免进程退出时卡顿。

    worker 阻塞在队列 ``get()`` 上，进程退出前必须显式取消，否则
    uvorn 会在 lifespan shutdown 阶段等待它们直到超时。
    """
    global _worker_tasks
    if not _worker_tasks:
        return
    for task in _worker_tasks:
        if not task.done():
            task.cancel()
    await asyncio.gather(*_worker_tasks, return_exceptions=True)
    _worker_tasks = []
    logger.info("Scrape workers cancelled")
