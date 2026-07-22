"""History and logging data models."""

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel


class TaskStatus(str, Enum):
    """Task execution status."""

    SUCCESS = "success"
    FAILED = "failed"
    TIMEOUT = "timeout"  # 任务超时
    CANCELLED = "cancelled"
    SKIPPED = "skipped"
    REPLACED = "replaced"  # 已由新的重试任务替代
    PENDING_ACTION = "pending_action"  # 待处理（需要用户手动处理）
    RUNNING = "running"  # 正在处理中


class TaskSource(str, Enum):
    """任务来源类型"""

    MANUAL = "manual"  # 手动创建
    WATCHER = "watcher"  # 文件监控触发


class ConflictType(str, Enum):
    """冲突类型枚举"""

    NEED_SELECTION = "need_selection"  # 多个搜索结果需要选择
    NEED_SEASON_EPISODE = "need_season_episode"  # 需要输入季/集号
    FILE_CONFLICT = "file_conflict"  # 目标文件已存在
    NO_MATCH = "no_match"  # 未找到匹配，需手动输入 TMDB ID
    SEARCH_FAILED = "search_failed"  # 搜索失败，需手动输入 TMDB ID
    API_FAILED = "api_failed"  # API 失败，需手动输入 TMDB ID
    EMBY_CONFLICT = "emby_conflict"  # Emby 中已存在该集


class LogLevel(str, Enum):
    """Log entry level."""

    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"


class ScrapeLogEntry(BaseModel):
    """Single log entry in scrape process."""

    message: str
    level: LogLevel = LogLevel.SUCCESS


class ScrapeLogStep(BaseModel):
    """A step in the scrape process with multiple log entries."""

    name: str
    completed: bool = True
    logs: list[ScrapeLogEntry] = []


class HistoryRecord(BaseModel):
    """History record model."""

    id: str
    display_id: int
    task_name: str
    folder_path: str
    executed_at: datetime
    status: TaskStatus
    source: TaskSource = TaskSource.MANUAL  # 任务来源
    total_files: int
    success_count: int
    failed_count: int
    duration_seconds: float
    error_message: str | None = None
    manual_job_id: int | None = None
    scrape_job_id: str | None = None  # 关联的刮削任务ID
    title: str | None = None
    season_number: int | None = None
    episode_number: int | None = None


class HistoryRecordCreate(BaseModel):
    """Request model for creating a history record."""

    task_name: str
    folder_path: str
    status: TaskStatus
    source: TaskSource = TaskSource.MANUAL  # 任务来源
    total_files: int
    success_count: int
    failed_count: int
    duration_seconds: float
    error_message: str | None = None
    manual_job_id: int | None = None
    scrape_job_id: str | None = None  # 关联的刮削任务ID
    file_fingerprint: str | None = None  # 文件指纹，用于去重
    conflict_type: ConflictType | None = None
    conflict_data: dict[str, Any] | None = None
    # 刮削日志
    scrape_logs: list[ScrapeLogStep] = []


class HistoryListResponse(BaseModel):
    """Response model for history list."""

    records: list[HistoryRecord]
    total: int


class HistoryExportResponse(BaseModel):
    """Response model for history export."""

    content: str
    filename: str


class HistoryRecordDetail(HistoryRecord):
    """Detailed history record with metadata."""

    # 剧集元数据
    title: str | None = None  # 剧名
    original_title: str | None = None  # 原标题
    plot: str | None = None  # 剧集简介
    tags: list[str] = []
    # 季/集信息
    season_number: int | None = None  # 季号
    episode_number: int | None = None  # 集号
    episode_title: str | None = None  # 集标题
    episode_overview: str | None = None  # 集简介
    episode_still_url: str | None = None  # 集封面地址
    episode_air_date: str | None = None  # 集发行日期
    # 图片
    cover_url: str | None = None
    poster_url: str | None = None
    thumb_url: str | None = None
    # 其他信息
    release_date: str | None = None
    rating: float | None = None
    votes: int | None = None
    translator: str | None = None
    # 刮削日志
    scrape_logs: list[ScrapeLogStep] = []
    # 冲突处理
    conflict_type: ConflictType | None = None  # 冲突类型
    conflict_data: dict[str, Any] | None = None  # 冲突上下文数据
