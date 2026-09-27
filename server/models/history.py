"""History and logging data models."""

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel


class TaskStatus(str, Enum):
    """Task execution status."""

    SUCCESS = "success"
    FAILED = "failed"
    TIMEOUT = "timeout"  # 任务超时
    CANCELLED = "cancelled"
    SKIPPED = "skipped"
    DELETED = "deleted"  # 已删除（保留历史记录，允许后续追溯）
    PENDING_ACTION = "pending_action"  # 待处理（需要用户手动处理）
    RUNNING = "running"  # 正在处理中


class TaskSource(str, Enum):
    """任务来源类型"""

    MANUAL = "manual"  # 手动创建
    WATCHER = "watcher"  # 文件监控触发


class HistoryConflictType(str, Enum):
    """历史记录冲突处理类型。

    与 ``models.emby.ConflictType``（Emby 检测结果）区分——两者值域不同。
    """

    NEED_SELECTION = "need_selection"  # 多个搜索结果需要选择
    NEED_SEASON_EPISODE = "need_season_episode"  # 需要输入季/集号
    FILE_CONFLICT = "file_conflict"  # 目标文件已存在
    NO_MATCH = "no_match"  # 未找到匹配，需手动输入 TMDB ID
    SEARCH_FAILED = "search_failed"  # 搜索失败，需手动输入 TMDB ID
    API_FAILED = "api_failed"  # API 失败，需手动输入 TMDB ID
    EMBY_CONFLICT = "emby_conflict"  # Emby 中已存在该集


class ScrapeLogLevel(str, Enum):
    """刮削步骤日志级别。

    与 ``models.log.LogLevel``（应用日志级别）区分——两者值域不同。
    """

    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"


class ScrapeLogEntry(BaseModel):
    """Single log entry in scrape process."""

    message: str
    level: ScrapeLogLevel = ScrapeLogLevel.SUCCESS


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
    started_at: datetime | None = None  # 本次执行开始时间（running 期间前端据此实时计时）
    error_message: str | None = None
    manual_job_id: int | None = None
    scrape_job_id: str | None = None  # 关联的刮削任务ID
    title: str | None = None
    tmdb_id: int | None = None  # 已确定的 TMDB ID（重试时直接沿用，不必重新搜索）
    season_number: int | None = None
    episode_number: int | None = None
    # 超时定位（仅 status=timeout 时写入）
    timeout_step: str | None = None  # 超时停在哪一步（如「搜索 TMDB」）
    timeout_seconds: int | None = None  # 本次执行的超时阈值，取自系统设置「任务超时」


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
    started_at: datetime | None = None  # 本次执行开始时间（running 期间前端据此实时计时）
    error_message: str | None = None
    manual_job_id: int | None = None
    scrape_job_id: str | None = None  # 关联的刮削任务ID
    file_fingerprint: str | None = None  # 文件指纹，用于去重
    conflict_type: HistoryConflictType | None = None
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
    tmdb_id: int | None = None  # 已确定的 TMDB ID（重试时直接沿用）
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
    conflict_type: HistoryConflictType | None = None  # 冲突类型
    conflict_data: dict[str, Any] | None = None  # 冲突上下文数据
    # 超时定位（仅 status=TIMEOUT 时写入）
    timeout_step: str | None = None  # 超时停在哪一步（如「搜索 TMDB」）
    timeout_seconds: int | None = None  # 本次执行的超时阈值，取自系统设置「任务超时」


class HistoryFileRole(str, Enum):
    """文件在记录中的角色。"""

    SOURCE = "source"  # 刮削前的原始文件
    ORGANIZED = "organized"  # 整理后的视频文件（刮削产物）
    METADATA = "metadata"  # 刮削产物里的元数据（nfo / 图片），与视频同属「刮削产物」范围


class HistoryFileEntry(BaseModel):
    """记录关联的单个文件（删除确认弹窗与撤销展示的依据）。"""

    path: str
    role: HistoryFileRole
    exists: bool
    size: int = 0  # 字节；文件不存在时为 0
    deletable: bool = False  # 是否允许删除（目录、越界路径、不存在的文件都为 False）
    reason: str | None = None  # 不可删除的原因，直接展示给用户


class HistoryFileListResponse(BaseModel):
    """记录的文件清单。"""

    record_id: str
    folder_path: str
    files: list[HistoryFileEntry] = []


class HistoryFileDeleteRequest(BaseModel):
    """删除文件请求。scope 语义：source 源文件 / organized 刮削产物 / all 两者。"""

    scope: Literal["source", "organized", "all"] = "all"


class HistoryFileDeleteResult(BaseModel):
    """单个文件的删除结果（失败逐条回报，不做整体回滚）。"""

    path: str
    role: HistoryFileRole
    deleted: bool
    reason: str | None = None


class HistoryFileDeleteResponse(BaseModel):
    """删除文件响应。"""

    success: bool
    deleted: int
    failed: int
    results: list[HistoryFileDeleteResult] = []
    message: str
    # 删掉源文件后对应登记行会一并移除，故是否可撤销由后端判定后告诉前端
    undoable: bool = False


class UndoResponse(BaseModel):
    """撤销最近一次操作响应。"""

    success: bool
    undone: bool
    kind: str | None = None  # 被撤销的操作类型
    restored: int = 0  # 恢复的记录条数
    message: str
