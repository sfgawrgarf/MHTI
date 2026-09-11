"""Runtime queue and worker observability models."""

from datetime import datetime

from pydantic import BaseModel, Field


class QueueRuntimeMetrics(BaseModel):
    """Persisted and in-memory state for one job queue."""

    status_counts: dict[str, int] = Field(default_factory=dict)
    queued_in_memory: int = 0
    active_tasks: int = 0
    worker_count: int = 0
    concurrency_limit: int = 0
    oldest_pending_at: datetime | None = None
    oldest_pending_seconds: float | None = None


class FileIORuntimeMetrics(BaseModel):
    """Current use of the bounded local filesystem executor."""

    workers: int = 2
    active: int = 0
    waiting: int = 0


class JobRuntimeMetrics(BaseModel):
    """Combined runtime view returned to the administration UI."""

    generated_at: datetime
    manual: QueueRuntimeMetrics
    scrape: QueueRuntimeMetrics
    file_io: FileIORuntimeMetrics
