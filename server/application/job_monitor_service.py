"""Aggregate persisted queue counts and live worker state."""

from datetime import datetime
from pathlib import Path

from server.application.file_io import get_file_io_snapshot
from server.application.manual_job_service import get_manual_runtime_state
from server.application.scrape_job_service import get_scrape_runtime_state
from server.infrastructure.db import DATABASE_PATH, db_context
from server.models.job_runtime import (
    FileIORuntimeMetrics,
    JobRuntimeMetrics,
    QueueRuntimeMetrics,
)


class JobMonitorService:
    """Build one consistent administration snapshot from existing state."""

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or DATABASE_PATH

    async def get_metrics(self) -> JobRuntimeMetrics:
        """Read persisted queue counts and attach in-memory worker state."""
        if self.db_path != DATABASE_PATH:
            raise ValueError("运行时观测暂不支持切换独立数据库")

        async with db_context() as db:
            cursor = await db.execute(
                """
                SELECT 'manual' AS queue, status, COUNT(*) AS count,
                       MIN(CASE WHEN status = 'pending' THEN created_at END) AS oldest
                FROM manual_jobs GROUP BY status
                UNION ALL
                SELECT 'scrape' AS queue, status, COUNT(*) AS count,
                       MIN(CASE WHEN status = 'pending' THEN created_at END) AS oldest
                FROM scrape_jobs GROUP BY status
                """
            )
            rows = await cursor.fetchall()

        generated_at = datetime.now()
        queues: dict[str, dict] = {
            "manual": {"status_counts": {}, "oldest_pending_at": None},
            "scrape": {"status_counts": {}, "oldest_pending_at": None},
        }
        for row in rows:
            queue = queues[str(row["queue"])]
            status = str(row["status"])
            queue["status_counts"][status] = int(row["count"])
            if status == "pending" and row["oldest"]:
                queue["oldest_pending_at"] = datetime.fromisoformat(row["oldest"])

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
