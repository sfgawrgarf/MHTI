"""Scheduled task data models."""

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, StringConstraints

NonEmptyString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class ScheduledTask(BaseModel):
    """Scheduled task model."""

    id: str
    name: str
    folder_path: str
    cron_expression: str
    enabled: bool = True
    last_run: datetime | None = None
    last_attempt: datetime | None = None
    last_status: str | None = None
    last_error: str | None = None
    retry_count: int = 0
    next_run: datetime | None = None
    created_at: datetime | None = None


class ScheduledTaskCreate(BaseModel):
    """Request model for creating a scheduled task."""

    name: NonEmptyString
    folder_path: NonEmptyString
    cron_expression: NonEmptyString
    enabled: bool = True


class ScheduledTaskUpdate(BaseModel):
    """Request model for updating a scheduled task."""

    name: NonEmptyString | None = None
    folder_path: NonEmptyString | None = None
    cron_expression: NonEmptyString | None = None
    enabled: bool | None = None


class ScheduledTaskResponse(BaseModel):
    """Response model for scheduled task."""

    id: str
    name: str
    folder_path: str
    cron_expression: str
    enabled: bool
    last_run: datetime | None
    last_attempt: datetime | None
    last_status: str | None
    last_error: str | None
    retry_count: int
    next_run: datetime | None
    created_at: datetime | None


class ScheduledTaskListResponse(BaseModel):
    """Response model for scheduled task list."""

    tasks: list[ScheduledTaskResponse]
    total: int
