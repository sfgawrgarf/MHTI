"""Aggregate live job queue and filesystem executor metrics."""

import asyncio
from datetime import datetime
from pathlib import Path

from server.core.database import DATABASE_PATH
from server.models.job_runtime import (
    FileIORuntimeMetrics,
    JobRuntimeMetrics,
    QueueRuntimeMetrics,
)
from server.services.file_io import get_file_io_snapshot
from server.services.manual_job_service import ManualJobService
from server.services.scrape_job_service import ScrapeJobService


class JobMonitorService:
    """Build one consistent administration snapshot from existing state."""

    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or DATABASE_PATH

    async def get_metrics(self) -> JobRuntimeMetrics:
        manual, scrape = await asyncio.gather(
            ManualJobService(self.db_path).get_runtime_metrics(),
            ScrapeJobService(self.db_path).get_runtime_metrics(),
        )
        return JobRuntimeMetrics(
            generated_at=datetime.now(),
            manual=QueueRuntimeMetrics(**manual),
            scrape=QueueRuntimeMetrics(**scrape),
            file_io=FileIORuntimeMetrics(**get_file_io_snapshot()),
        )
