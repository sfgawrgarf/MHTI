"""Scrape job data models - 文件刮削任务模型"""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel

from server.models.manual_job import ManualJobAdvancedSettings
from server.models.organize import OrganizeMode
from server.models.storage import StorageLocator


class ScrapeJobSource(str, Enum):
    """刮削任务来源"""

    MANUAL = "manual"  # 来自手动任务
    WATCHER = "watcher"  # 来自监控任务


class ScrapeJobStatus(str, Enum):
    """刮削任务状态"""

    PENDING = "pending"  # 等待执行
    RUNNING = "running"  # 执行中
    SUCCESS = "success"  # 成功
    FAILED = "failed"  # 失败
    TIMEOUT = "timeout"  # 超时
    CANCELLED = "cancelled"  # 已取消
    SKIPPED = "skipped"  # 用户跳过
    REPLACED = "replaced"  # 已由新的重试任务替代
    PENDING_ACTION = "pending_action"  # 需要用户处理


class ScrapeJob(BaseModel):
    """单文件刮削任务"""

    id: str
    file_path: str  # 源文件路径
    output_dir: str  # 输出目录
    metadata_dir: str | None = None  # 元数据目录
    file_locator: StorageLocator | None = None
    output_locator: StorageLocator | None = None
    metadata_locator: StorageLocator | None = None
    allow_local_output: bool = False
    link_mode: OrganizeMode | None = None  # 整理模式
    source: ScrapeJobSource  # 来源
    source_id: int | None = None  # 来源任务ID (ManualJob.id)
    advanced_settings: ManualJobAdvancedSettings | None = None  # 高级设置
    status: ScrapeJobStatus = ScrapeJobStatus.PENDING
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error_message: str | None = None
    history_record_id: str | None = None  # 关联的历史记录ID
    replaces_job_id: str | None = None
    replaced_by_job_id: str | None = None


class ScrapeJobCreate(BaseModel):
    """创建刮削任务请求"""

    file_path: str
    output_dir: str
    metadata_dir: str | None = None
    file_locator: StorageLocator | None = None
    output_locator: StorageLocator | None = None
    metadata_locator: StorageLocator | None = None
    allow_local_output: bool = False
    link_mode: OrganizeMode | None = None  # 整理模式
    source: ScrapeJobSource = ScrapeJobSource.MANUAL
    source_id: int | None = None
    advanced_settings: ManualJobAdvancedSettings | None = None  # 高级设置
    replaces_job_id: str | None = None


class ScrapeJobListResponse(BaseModel):
    """刮削任务列表响应"""

    jobs: list[ScrapeJob]
    total: int
