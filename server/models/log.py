"""日志数据模型。"""

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class LogLevel(str, Enum):
    """日志级别枚举。"""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class LogEntry(BaseModel):
    """日志条目模型。"""

    id: int
    timestamp: datetime
    level: LogLevel
    logger: str
    message: str
    extra_data: dict[str, Any] | None = None
    request_id: str | None = None
    user_id: int | None = None


class LogEntryCreate(BaseModel):
    """创建日志条目的请求模型。"""

    timestamp: datetime
    level: LogLevel
    logger: str
    message: str
    extra_data: dict[str, Any] | None = None
    request_id: str | None = None
    user_id: int | None = None


class LogConfig(BaseModel):
    """日志配置模型。"""

    log_level: LogLevel = LogLevel.INFO
    console_enabled: bool = True
    file_enabled: bool = True
    db_enabled: bool = True
    max_file_size_mb: int = Field(default=10, ge=1, le=100)
    max_file_count: int = Field(default=5, ge=1, le=20)
    db_retention_days: int = Field(default=30, ge=1, le=365)
    realtime_enabled: bool = True


class LogConfigUpdate(BaseModel):
    """更新日志配置的请求模型。"""

    log_level: LogLevel | None = None
    console_enabled: bool | None = None
    file_enabled: bool | None = None
    db_enabled: bool | None = None
    max_file_size_mb: int | None = Field(default=None, ge=1, le=100)
    max_file_count: int | None = Field(default=None, ge=1, le=20)
    db_retention_days: int | None = Field(default=None, ge=1, le=365)
    realtime_enabled: bool | None = None


class LogQuery(BaseModel):
    """日志查询参数模型。"""

    level: LogLevel | None = None
    logger: str | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    search: str | None = None
    # API list pagination is capped at 100; exports intentionally allow more.
    limit: int = Field(default=100, ge=1, le=10000)
    offset: int = Field(default=0, ge=0)


class LogStats(BaseModel):
    """日志统计信息模型。"""

    total: int
    by_level: dict[str, int]
    by_logger: dict[str, int]
    oldest_entry: datetime | None = None
    newest_entry: datetime | None = None


class LogListResponse(BaseModel):
    """日志列表响应模型。"""

    items: list[LogEntry]
    total: int
    limit: int
    offset: int


class ClearLogsResponse(BaseModel):
    """清理日志响应模型。"""

    deleted: int
    message: str
