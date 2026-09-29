"""Cancellation checkpoints shared by filesystem adapters and their executor."""

from contextvars import ContextVar
from threading import Event


class FileIOCancelled(Exception):
    """A synchronous operation stopped at a safe cancellation checkpoint."""


cancel_event: ContextVar[Event | None] = ContextVar("file_io_cancel", default=None)


def check_file_cancelled() -> None:
    event = cancel_event.get()
    if event is not None and event.is_set():
        raise FileIOCancelled("文件操作已取消")
