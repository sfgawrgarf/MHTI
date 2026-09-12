"""Aggregate live job queue and filesystem executor metrics."""

from datetime import datetime
from pathlib import Path

from server.core.db.connection import db_connection
from server.core.database import DATABASE_PATH
from server.models.job_runtime import (
    FileIORuntimeMetrics,
    JobRuntimeMetrics,
    QueueRuntimeMetrics,
)
from server.services.file_io import get_file_io_snapshot
from server.services.manual_job_service import get_manual_runtime_state
from server.services.scrape_job_service import get_scrape_runtime_state


class JobMonitorService:
    """Build one consistent administration snapshot from existing state."""

    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or DATABASE_PATH

    async def get_metrics(self) -> JobRuntimeMetrics:
        """Read both persisted queues in one query and attach live state."""
        async with db_connection(self.db_path) as db:
            cursor = await db.execute(
                """SELECT 'manual', status, COUNT(*),
                          MIN(CASE WHEN status = 'pending' THEN created_at END)
                   FROM manual_jobs GROUP BY status
                   UNION ALL
                   SELECT 'scrape', status, COUNT(*),
                          MIN(CASE WHEN status = 'pending' THEN created_at END)
                   FROM scrape_jobs GROUP BY status"""
            )
            rows = await cursor.fetchall()

        generated_at = datetime.now()
        queues: dict[str, dict] = {
            "manual": {"status_counts": {}, "oldest_pending_at": None},
            "scrape": {"status_counts": {}, "oldest_pending_at": None},
        }
        for queue_name, status, count, oldest_pending in rows:
            queue = queues[str(queue_name)]
            queue["status_counts"][str(status)] = int(count)
            if status == "pending" and oldest_pending:
                queue["oldest_pending_at"] = datetime.fromisoformat(oldest_pending)

        def build_queue(name: str, live: dict[str, int]) -> QueueRuntimeMetrics:
            persisted = queues[name]
            oldest = persisted["oldest_pending_at"]
            return QueueRuntimeMetrics(
                status_counts=persisted["status_counts"],
                oldest_pending_at=oldest,
                oldest_pending_seconds=(
                    max(0.0, (generated_at - oldest).total_seconds())
                    if oldest is not None
                    else None
                ),
                **live,
            )

        manual_counts = queues["manual"]["status_counts"]
        scrape_counts = queues["scrape"]["status_counts"]
        return JobRuntimeMetrics(
            generated_at=generated_at,
            manual=build_queue(
                "manual",
                get_manual_runtime_state(manual_counts.get("pending", 0)),
            ),
            scrape=build_queue(
                "scrape",
                get_scrape_runtime_state(scrape_counts.get("pending", 0)),
            ),
            file_io=FileIORuntimeMetrics(**get_file_io_snapshot()),
        )
