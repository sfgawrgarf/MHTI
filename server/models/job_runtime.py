"""后台任务队列与文件 I/O 的运行时观测模型。"""

from datetime import datetime

from pydantic import BaseModel, Field


class QueueRuntimeMetrics(BaseModel):
    """一个任务队列的持久化和内存状态。"""

    status_counts: dict[str, int] = Field(default_factory=dict)
    queued_in_memory: int = 0
    active_tasks: int = 0
    worker_count: int = 0
    concurrency_limit: int = 0
    oldest_pending_at: datetime | None = None
    oldest_pending_seconds: float | None = None


class FileIORuntimeMetrics(BaseModel):
    """本地文件 I/O 执行器的当前占用。"""

    workers: int = 2
    active: int = 0
    waiting: int = 0


class JobRuntimeMetrics(BaseModel):
    """管理端展示的合并运行时视图。"""

    generated_at: datetime
    manual: QueueRuntimeMetrics
    scrape: QueueRuntimeMetrics
    file_io: FileIORuntimeMetrics
