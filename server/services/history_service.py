"""History service for managing scrape history records."""

import asyncio
import csv
import io
import json
import uuid
from datetime import datetime
from pathlib import Path

import aiosqlite

from server.core.database import DATABASE_PATH, _configure_connection
from server.models.history import (
    ConflictType,
    HistoryRecord,
    HistoryRecordCreate,
    HistoryRecordDetail,
    ScrapeLogStep,
    TaskSource,
    TaskStatus,
)
from server.services.websocket_manager import get_notifier

# 内存日志缓存：record_id -> list[ScrapeLogStep]
_log_cache: dict[str, list[ScrapeLogStep]] = {}
# SSE 订阅者：record_id -> list[asyncio.Queue]
_log_subscribers: dict[str, list[asyncio.Queue]] = {}


class HistoryService:
    """Service for managing scrape history records."""

    def __init__(self, db_path: Path | None = None):
        """Initialize history service."""
        self.db_path = db_path or DATABASE_PATH

    async def _ensure_db(self) -> None:
        """Ensure database directory exists and run migrations."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.db_path) as db:
            await _configure_connection(db)
            # 添加新列（如果不存在）- 迁移逻辑
            new_columns = [
                ("display_id", "INTEGER"),
                ("manual_job_id", "INTEGER"),
                ("title", "TEXT"),
                ("original_title", "TEXT"),
                ("plot", "TEXT"),
                ("tags", "TEXT"),
                ("cover_url", "TEXT"),
                ("poster_url", "TEXT"),
                ("thumb_url", "TEXT"),
                ("release_date", "TEXT"),
                ("rating", "REAL"),
                ("votes", "INTEGER"),
                ("translator", "TEXT"),
                ("scrape_logs", "TEXT"),
                ("conflict_type", "TEXT"),
                ("conflict_data", "TEXT"),
                # 季/集信息
                ("season_number", "INTEGER"),
                ("episode_number", "INTEGER"),
                ("episode_title", "TEXT"),
                ("episode_overview", "TEXT"),
                ("episode_still_url", "TEXT"),
                ("episode_air_date", "TEXT"),
                ("source", "TEXT DEFAULT 'manual'"),
                ("scrape_job_id", "TEXT"),
                ("file_fingerprint", "TEXT"),  # 文件指纹，用于去重
            ]
            for col_name, col_type in new_columns:
                try:
                    await db.execute(
                        f"ALTER TABLE history_records ADD COLUMN {col_name} {col_type}"
                    )
                except Exception:
                    pass  # 列已存在
            await db.commit()

    async def create_record(self, record: HistoryRecordCreate) -> HistoryRecord:
        """Create a new history record."""
        await self._ensure_db()

        record_id = str(uuid.uuid4())[:8]
        now = datetime.now()

        # 序列化 conflict_data
        conflict_data_json = None
        if record.conflict_data is not None:
            conflict_data_json = json.dumps(record.conflict_data, ensure_ascii=False)

        # 序列化 scrape_logs
        scrape_logs_json = None
        if record.scrape_logs:
            scrape_logs_json = json.dumps(
                [log.model_dump() for log in record.scrape_logs],
                ensure_ascii=False
            )

        async with aiosqlite.connect(self.db_path) as db:
            await _configure_connection(db)
            # 获取下一个 display_id
            cursor = await db.execute(
                "SELECT COALESCE(MAX(display_id), 0) + 1 FROM history_records"
            )
            row = await cursor.fetchone()
            display_id = row[0] if row else 1

            await db.execute(
                """
                INSERT INTO history_records
                (id, display_id, task_name, folder_path, executed_at, status, source, total_files,
                 success_count, failed_count, duration_seconds, error_message,
                 manual_job_id, scrape_job_id, file_fingerprint, conflict_type, conflict_data, scrape_logs)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record_id,
                    display_id,
                    record.task_name,
                    record.folder_path,
                    now.isoformat(),
                    record.status.value,
                    record.source.value,
                    record.total_files,
                    record.success_count,
                    record.failed_count,
                    record.duration_seconds,
                    record.error_message,
                    record.manual_job_id,
                    record.scrape_job_id,
                    record.file_fingerprint,
                    record.conflict_type.value if record.conflict_type else None,
                    conflict_data_json,
                    scrape_logs_json,
                ),
            )
            await db.commit()

        result = HistoryRecord(
            id=record_id,
            display_id=display_id,
            task_name=record.task_name,
            folder_path=record.folder_path,
            executed_at=now,
            status=record.status,
            source=record.source,
            total_files=record.total_files,
            success_count=record.success_count,
            failed_count=record.failed_count,
            duration_seconds=record.duration_seconds,
            error_message=record.error_message,
            manual_job_id=record.manual_job_id,
            scrape_job_id=record.scrape_job_id,
        )

        # 发送 WebSocket 通知
        notifier = get_notifier()
        await notifier.notify_history_created(result.model_dump(mode="json"))

        return result

    async def list_records(
        self,
        limit: int = 100,
        offset: int = 0,
        manual_job_id: int | None = None,
        search: str | None = None,
        status: TaskStatus | None = None,
    ) -> tuple[list[HistoryRecord], int]:
        """List history records with pagination, search and status filter.

        优化策略：
        - 第一页使用快速模式，避免 COUNT 查询
        - 后续页面使用 COUNT 查询确保分页正确
        """
        await self._ensure_db()

        conditions = []
        params = []
        if manual_job_id is not None:
            conditions.append("manual_job_id = ?")
            params.append(manual_job_id)
        if search:
            # 搜索 title, folder_path, task_name
            conditions.append("(title LIKE ? OR folder_path LIKE ? OR task_name LIKE ?)")
            search_pattern = f"%{search}%"
            params.extend([search_pattern, search_pattern, search_pattern])
        if status is not None:
            conditions.append("status = ?")
            params.append(status.value)

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        async with aiosqlite.connect(self.db_path) as db:
            await _configure_connection(db)
            db.row_factory = aiosqlite.Row

            # 优化：第一页且无筛选条件时使用快速模式
            is_first_page = offset == 0
            has_filters = bool(conditions)

            if is_first_page and not has_filters:
                # 快速模式：第一页且无筛选时，假设 total 足够大
                # 多查一条判断是否有更多数据
                cursor = await db.execute(
                    f"""
                    SELECT * FROM history_records
                    ORDER BY executed_at DESC
                    LIMIT ? OFFSET ?
                    """,
                    [limit + 1, offset],
                )
                rows = await cursor.fetchall()

                # 判断是否有更多数据
                has_more = len(rows) > limit
                records = [self._row_to_record(row) for row in rows[:limit]]

                # 如果数据不足 limit+1 条，说明已经到最后一页
                if len(rows) <= limit:
                    total = len(records)
                else:
                    # 需要获取实际总数（仅当有更多数据时）
                    cursor = await db.execute(
                        "SELECT COUNT(*) as count FROM history_records"
                    )
                    row = await cursor.fetchone()
                    total = row["count"] if row else len(records)
            else:
                # 标准模式：需要 COUNT 查询
                cursor = await db.execute(
                    f"SELECT COUNT(*) as count FROM history_records {where_clause}",
                    params,
                )
                row = await cursor.fetchone()
                total = row["count"] if row else 0

                # Get records
                cursor = await db.execute(
                    f"""
                    SELECT * FROM history_records {where_clause}
                    ORDER BY executed_at DESC
                    LIMIT ? OFFSET ?
                    """,
                    params + [limit, offset],
                )
                rows = await cursor.fetchall()
                records = [self._row_to_record(row) for row in rows]

        return records, total

    async def get_record(self, record_id: str) -> HistoryRecordDetail | None:
        """Get a history record by ID with full details."""
        await self._ensure_db()

        async with aiosqlite.connect(self.db_path) as db:
            await _configure_connection(db)
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM history_records WHERE id = ?",
                (record_id,),
            )
            row = await cursor.fetchone()

        if row is None:
            return None

        return self._row_to_detail(row)

    async def get_existing_fingerprints(self, fingerprints: list[str]) -> set[str]:
        """
        查询已存在的文件指纹.

        Args:
            fingerprints: 要查询的指纹列表

        Returns:
            已存在的指纹集合
        """
        if not fingerprints:
            return set()

        await self._ensure_db()

        async with aiosqlite.connect(self.db_path) as db:
            await _configure_connection(db)
            placeholders = ",".join("?" * len(fingerprints))
            cursor = await db.execute(
                f"SELECT DISTINCT file_fingerprint FROM history_records WHERE file_fingerprint IN ({placeholders})",
                fingerprints,
            )
            rows = await cursor.fetchall()

        return {row[0] for row in rows if row[0]}

    async def delete_record(self, record_id: str) -> bool:
        """Delete a history record and its associated scrape job."""
        await self._ensure_db()

        async with aiosqlite.connect(self.db_path) as db:
            await _configure_connection(db)
            # 先获取关联的 scrape_job_id
            cursor = await db.execute(
                "SELECT scrape_job_id FROM history_records WHERE id = ?",
                (record_id,),
            )
            row = await cursor.fetchone()
            scrape_job_id = row[0] if row else None

            # 删除历史记录
            cursor = await db.execute(
                "DELETE FROM history_records WHERE id = ?",
                (record_id,),
            )
            deleted = cursor.rowcount > 0

            # 同时删除关联的 scrape_job
            if scrape_job_id:
                await db.execute(
                    "DELETE FROM scrape_jobs WHERE id = ?",
                    (scrape_job_id,),
                )

            await db.commit()

            # 发送 WebSocket 通知
            if deleted:
                notifier = get_notifier()
                await notifier.notify_history_deleted(record_id)

            return deleted

    async def update_record(
        self,
        record_id: str,
        status: TaskStatus | None = None,
        error_message: str | None = None,
        conflict_type: ConflictType | None = None,
        conflict_data: dict | None = None,
        title: str | None = None,
        original_title: str | None = None,
        plot: str | None = None,
        poster_url: str | None = None,
        release_date: str | None = None,
        rating: float | None = None,
        tags: list[str] | None = None,
        season_number: int | None = None,
        episode_number: int | None = None,
        episode_title: str | None = None,
        episode_overview: str | None = None,
        episode_still_url: str | None = None,
        episode_air_date: str | None = None,
    ) -> bool:
        """Update a history record's status and conflict info."""
        await self._ensure_db()

        updates = []
        params = []

        if status is not None:
            updates.append("status = ?")
            params.append(status.value)
        if error_message is not None:
            updates.append("error_message = ?")
            params.append(error_message)
        if conflict_type is not None:
            updates.append("conflict_type = ?")
            params.append(conflict_type.value)
        if conflict_data is not None:
            updates.append("conflict_data = ?")
            params.append(json.dumps(conflict_data, ensure_ascii=False))
        if title is not None:
            updates.append("title = ?")
            params.append(title)
        if original_title is not None:
            updates.append("original_title = ?")
            params.append(original_title)
        if plot is not None:
            updates.append("plot = ?")
            params.append(plot)
        if poster_url is not None:
            updates.append("poster_url = ?")
            params.append(poster_url)
        if release_date is not None:
            updates.append("release_date = ?")
            params.append(release_date)
        if rating is not None:
            updates.append("rating = ?")
            params.append(rating)
        if tags is not None:
            updates.append("tags = ?")
            params.append(json.dumps(tags, ensure_ascii=False))
        if season_number is not None:
            updates.append("season_number = ?")
            params.append(season_number)
        if episode_number is not None:
            updates.append("episode_number = ?")
            params.append(episode_number)
        if episode_title is not None:
            updates.append("episode_title = ?")
            params.append(episode_title)
        if episode_overview is not None:
            updates.append("episode_overview = ?")
            params.append(episode_overview)
        if episode_still_url is not None:
            updates.append("episode_still_url = ?")
            params.append(episode_still_url)
        if episode_air_date is not None:
            updates.append("episode_air_date = ?")
            params.append(episode_air_date)

        if not updates:
            return False

        params.append(record_id)

        async with aiosqlite.connect(self.db_path) as db:
            await _configure_connection(db)
            cursor = await db.execute(
                f"UPDATE history_records SET {', '.join(updates)} WHERE id = ?",
                params,
            )
            # history_records is the user-visible state machine.  Keep its
            # linked worker row in lockstep in the *same transaction* so a
            # resolved, skipped, or retried record cannot leave a stale
            # pending_action scrape job behind.
            if status is not None:
                await db.execute(
                    """UPDATE scrape_jobs
                       SET status = ?,
                           finished_at = CASE WHEN ? IN ('pending', 'running')
                                              THEN finished_at
                                              ELSE COALESCE(finished_at, CURRENT_TIMESTAMP) END
                       WHERE history_record_id = ?""",
                    (status.value, status.value, record_id),
                )
            await db.commit()
            updated = cursor.rowcount > 0

        # 发送 WebSocket 通知
        if updated:
            notifier = get_notifier()
            update_data = {}
            if status is not None:
                update_data["status"] = status.value
            if title is not None:
                update_data["title"] = title
            if season_number is not None:
                update_data["season_number"] = season_number
            if episode_number is not None:
                update_data["episode_number"] = episode_number
            # 通知历史列表页（全局广播）
            await notifier.notify_history_updated(record_id, update_data)
            # 通知详情页订阅者（仅订阅了该记录的客户端）
            await notifier.notify_history_detail_update(record_id, update_data)

        return updated

    async def clear_records(self, before_days: int | None = None) -> int:
        """Clear history records and associated scrape jobs."""
        await self._ensure_db()

        async with aiosqlite.connect(self.db_path) as db:
            await _configure_connection(db)
            if before_days is not None:
                from datetime import timedelta
                cutoff = (datetime.now() - timedelta(days=before_days)).isoformat()
                # 先删除关联的 scrape_jobs
                await db.execute(
                    """DELETE FROM scrape_jobs WHERE id IN (
                        SELECT scrape_job_id FROM history_records
                        WHERE executed_at < ? AND scrape_job_id IS NOT NULL
                    )""",
                    (cutoff,),
                )
                cursor = await db.execute(
                    "DELETE FROM history_records WHERE executed_at < ?",
                    (cutoff,),
                )
            else:
                # 先删除关联的 scrape_jobs
                await db.execute(
                    """DELETE FROM scrape_jobs WHERE id IN (
                        SELECT scrape_job_id FROM history_records WHERE scrape_job_id IS NOT NULL
                    )"""
                )
                cursor = await db.execute("DELETE FROM history_records")
            await db.commit()
            count = cursor.rowcount

        # 发送 WebSocket 通知
        if count > 0:
            notifier = get_notifier()
            await notifier.notify_history_cleared(count)

        return count

    async def export_csv(self) -> str:
        """Export history records as CSV."""
        records, _ = await self.list_records(limit=10000)

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "ID", "任务名称", "文件夹路径", "执行时间", "状态",
            "总文件数", "成功数", "失败数", "耗时(秒)", "错误信息"
        ])

        for record in records:
            writer.writerow([
                record.id,
                record.task_name,
                record.folder_path,
                record.executed_at.isoformat(),
                record.status.value,
                record.total_files,
                record.success_count,
                record.failed_count,
                record.duration_seconds,
                record.error_message or "",
            ])

        return output.getvalue()

    def _row_to_record(self, row) -> HistoryRecord:
        """Convert database row to HistoryRecord."""
        # 兼容旧数据，source 可能不存在
        source_value = row["source"] if "source" in row.keys() else "manual"
        scrape_job_id = row["scrape_job_id"] if "scrape_job_id" in row.keys() else None
        return HistoryRecord(
            id=row["id"],
            display_id=row["display_id"] or 0,
            task_name=row["task_name"],
            folder_path=row["folder_path"],
            executed_at=datetime.fromisoformat(row["executed_at"]),
            status=TaskStatus(row["status"]),
            source=TaskSource(source_value),
            total_files=row["total_files"],
            success_count=row["success_count"],
            failed_count=row["failed_count"],
            duration_seconds=row["duration_seconds"],
            error_message=row["error_message"],
            manual_job_id=row["manual_job_id"],
            scrape_job_id=scrape_job_id,
            title=row["title"],
            season_number=row["season_number"],
            episode_number=row["episode_number"],
        )

    def _row_to_detail(self, row) -> HistoryRecordDetail:
        """Convert database row to HistoryRecordDetail."""
        # 解析 tags JSON
        tags = []
        if row["tags"]:
            try:
                tags = json.loads(row["tags"])
            except (json.JSONDecodeError, TypeError):
                pass

        # 解析 scrape_logs JSON
        scrape_logs = []
        if row["scrape_logs"]:
            try:
                logs_data = json.loads(row["scrape_logs"])
                scrape_logs = [ScrapeLogStep(**log) for log in logs_data]
            except (json.JSONDecodeError, TypeError):
                pass

        # 解析 conflict_type
        conflict_type = None
        if row["conflict_type"]:
            try:
                conflict_type = ConflictType(row["conflict_type"])
            except ValueError:
                pass

        # 解析 conflict_data JSON
        conflict_data = None
        if row["conflict_data"]:
            try:
                conflict_data = json.loads(row["conflict_data"])
            except (json.JSONDecodeError, TypeError):
                pass

        # 兼容旧数据，source 可能不存在
        source_value = row["source"] if "source" in row.keys() else "manual"
        scrape_job_id = row["scrape_job_id"] if "scrape_job_id" in row.keys() else None

        return HistoryRecordDetail(
            id=row["id"],
            display_id=row["display_id"] or 0,
            task_name=row["task_name"],
            folder_path=row["folder_path"],
            executed_at=datetime.fromisoformat(row["executed_at"]),
            status=TaskStatus(row["status"]),
            source=TaskSource(source_value),
            total_files=row["total_files"],
            success_count=row["success_count"],
            failed_count=row["failed_count"],
            duration_seconds=row["duration_seconds"],
            error_message=row["error_message"],
            manual_job_id=row["manual_job_id"],
            scrape_job_id=scrape_job_id,
            title=row["title"],
            original_title=row["original_title"],
            plot=row["plot"],
            tags=tags,
            season_number=row["season_number"],
            episode_number=row["episode_number"],
            episode_title=row["episode_title"],
            episode_overview=row["episode_overview"],
            episode_still_url=row["episode_still_url"],
            episode_air_date=row["episode_air_date"],
            cover_url=row["cover_url"],
            poster_url=row["poster_url"],
            thumb_url=row["thumb_url"],
            release_date=row["release_date"],
            rating=row["rating"],
            votes=row["votes"],
            translator=row["translator"],
            scrape_logs=scrape_logs,
            conflict_type=conflict_type,
            conflict_data=conflict_data,
        )

    async def update_scrape_logs(
        self,
        record_id: str,
        logs: list[ScrapeLogStep],
    ) -> None:
        """更新刮削日志并通知订阅者"""
        # 更新内存缓存
        _log_cache[record_id] = logs

        # 通知所有订阅者（旧的 SSE 订阅者）
        if record_id in _log_subscribers:
            for queue in _log_subscribers[record_id]:
                try:
                    queue.put_nowait(logs)
                except asyncio.QueueFull:
                    pass

        # 持久化到数据库
        await self._ensure_db()
        scrape_logs_json = json.dumps(
            [log.model_dump() for log in logs],
            ensure_ascii=False
        )
        async with aiosqlite.connect(self.db_path) as db:
            await _configure_connection(db)
            await db.execute(
                "UPDATE history_records SET scrape_logs = ? WHERE id = ?",
                (scrape_logs_json, record_id),
            )
            await db.commit()

        # 通过 WebSocket 推送日志更新（用于详情页实时刷新）
        notifier = get_notifier()
        await notifier.notify_history_detail_update(
            record_id,
            {"logs": [log.model_dump() for log in logs]}
        )

    async def subscribe_logs(self, record_id: str) -> asyncio.Queue:
        """订阅日志更新，返回一个队列用于接收更新"""
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)

        if record_id not in _log_subscribers:
            _log_subscribers[record_id] = []
        _log_subscribers[record_id].append(queue)

        # 如果缓存中有日志，立即发送
        if record_id in _log_cache:
            queue.put_nowait(_log_cache[record_id])

        return queue

    def unsubscribe_logs(self, record_id: str, queue: asyncio.Queue) -> None:
        """取消订阅日志更新"""
        if record_id in _log_subscribers:
            try:
                _log_subscribers[record_id].remove(queue)
                if not _log_subscribers[record_id]:
                    del _log_subscribers[record_id]
            except ValueError:
                pass

    def clear_log_cache(self, record_id: str) -> None:
        """清除日志缓存"""
        _log_cache.pop(record_id, None)

    async def flush_and_clear_log_cache(self, record_id: str) -> None:
        """保存缓存中的日志到数据库，然后清除缓存"""
        logs = _log_cache.get(record_id)
        if logs:
            await self.update_scrape_logs(record_id, logs)
        _log_cache.pop(record_id, None)

    async def update_record_on_success(
        self,
        record_id: str,
        folder_path: str,
        duration_seconds: float,
        title: str | None = None,
        original_title: str | None = None,
        plot: str | None = None,
        poster_url: str | None = None,
        release_date: str | None = None,
        rating: float | None = None,
        tags: list[str] | None = None,
        season_number: int | None = None,
        episode_number: int | None = None,
        episode_title: str | None = None,
        episode_overview: str | None = None,
        episode_still_url: str | None = None,
        episode_air_date: str | None = None,
    ) -> None:
        """更新成功记录的额外字段"""
        await self._ensure_db()

        tags_json = json.dumps(tags, ensure_ascii=False) if tags else None

        async with aiosqlite.connect(self.db_path) as db:
            await _configure_connection(db)
            await db.execute(
                """UPDATE history_records SET
                   status = ?, folder_path = ?, duration_seconds = ?, success_count = 1,
                   title = ?, original_title = ?, plot = ?, poster_url = ?,
                   release_date = ?, rating = ?, tags = ?,
                   season_number = ?, episode_number = ?, episode_title = ?,
                   episode_overview = ?, episode_still_url = ?, episode_air_date = ?
                   WHERE id = ?""",
                (TaskStatus.SUCCESS.value, folder_path, duration_seconds,
                 title, original_title, plot, poster_url,
                 release_date, rating, tags_json,
                 season_number, episode_number, episode_title,
                 episode_overview, episode_still_url, episode_air_date, record_id),
            )
            await db.commit()

        # 发送 WebSocket 通知
        notifier = get_notifier()
        update_data = {
            "status": TaskStatus.SUCCESS.value,
            "title": title,
            "season_number": season_number,
            "episode_number": episode_number,
        }
        # 通知历史列表页（全局广播）
        await notifier.notify_history_updated(record_id, update_data)
        # 通知详情页订阅者（仅订阅了该记录的客户端）
        await notifier.notify_history_detail_update(record_id, update_data)
