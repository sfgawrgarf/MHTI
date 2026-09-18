"""自定义日志处理器 - 将日志写入数据库。"""

import asyncio
import logging
import sys
from datetime import datetime
from threading import Lock
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from server.services.log_service import LogService


class DatabaseLogHandler(logging.Handler):
    """
    将日志写入数据库的处理器。

    特点：
    - 异步批量写入，不阻塞主线程
    - 自动缓冲，达到阈值或定时刷新
    - 支持额外数据字段（request_id, user_id）
    """

    def __init__(
        self,
        log_service: "LogService",
        batch_size: int = 50,
        flush_interval: float = 5.0,
    ) -> None:
        """
        初始化数据库日志处理器。

        Args:
            log_service: 日志服务实例
            batch_size: 批量写入阈值
            flush_interval: 刷新间隔（秒）
        """
        super().__init__()
        self._log_service = log_service
        self._batch: list[dict[str, Any]] = []
        self._batch_size = batch_size
        self._flush_interval = flush_interval
        self._loop: asyncio.AbstractEventLoop | None = None
        self._periodic_task: asyncio.Task[None] | None = None
        self._immediate_task: asyncio.Task[bool] | None = None
        self._flush_lock = asyncio.Lock()
        self._batch_guard = Lock()
        self._flush_requested = False
        self._started = False
        self._accepting = True

    def start(self) -> None:
        """启动定时刷新任务。"""
        if self._started:
            return

        self._loop = asyncio.get_running_loop()
        with self._batch_guard:
            self._accepting = True
            self._started = True
        self._periodic_task = self._loop.create_task(self._periodic_flush())

    @property
    def pending_count(self) -> int:
        """Return the number of entries still waiting for persistence."""
        with self._batch_guard:
            return len(self._batch)

    async def aclose(self, retries: int = 3) -> bool:
        """Stop background work and wait for the final buffered write.

        The handler should be removed from the root logger before this method is
        called so no new records can arrive while shutdown drains the buffer.
        """
        with self._batch_guard:
            self._accepting = False
            self._started = False

        if self._periodic_task is not None:
            self._periodic_task.cancel()
            try:
                await self._periodic_task
            except asyncio.CancelledError:
                pass
            self._periodic_task = None

        if self._immediate_task is not None:
            try:
                await self._immediate_task
            except asyncio.CancelledError:
                pass
            self._immediate_task = None

        attempts = max(1, retries)
        for attempt in range(attempts):
            if not self.pending_count:
                self._loop = None
                return True
            if await self._flush():
                self._loop = None
                return True
            if attempt + 1 < attempts:
                await asyncio.sleep(0.05 * (attempt + 1))

        remaining = self.pending_count
        sys.stderr.write(
            f"Failed to persist {remaining} buffered database log entries during shutdown\n"
        )
        self._loop = None
        return False

    async def _periodic_flush(self) -> None:
        """定时刷新日志到数据库。"""
        while self._started:
            try:
                await asyncio.sleep(self._flush_interval)
                await self._flush()
            except asyncio.CancelledError:
                break

    async def _flush(self) -> bool:
        """将缓冲的日志批量写入数据库。"""
        async with self._flush_lock:
            with self._batch_guard:
                if not self._batch:
                    return True
                batch = self._batch
                self._batch = []

            try:
                await self._log_service.batch_insert(batch)
            except asyncio.CancelledError:
                with self._batch_guard:
                    self._batch[0:0] = batch
                raise
            except Exception as exc:
                # New entries may have arrived while the database call was in
                # flight. Restore the failed batch in front of them so the next
                # periodic flush retries every record in its original order.
                with self._batch_guard:
                    self._batch[0:0] = batch
                sys.stderr.write(f"Failed to flush logs to database: {exc}\n")
                return False
            return True

    def _request_flush(self) -> None:
        """Request one coalesced immediate flush from any logging thread."""
        loop = self._loop
        if loop is None or not loop.is_running():
            return

        with self._batch_guard:
            if not self._started or self._flush_requested:
                return
            self._flush_requested = True
        loop.call_soon_threadsafe(self._launch_immediate_flush)

    def _launch_immediate_flush(self) -> None:
        """Create the coalesced flush task on the owning event loop."""
        if not self._started:
            with self._batch_guard:
                self._flush_requested = False
            return
        if self._immediate_task is None or self._immediate_task.done():
            self._immediate_task = asyncio.create_task(self._run_immediate_flush())

    async def _run_immediate_flush(self) -> bool:
        succeeded = await self._flush()
        request_another = False
        with self._batch_guard:
            self._flush_requested = False
            if succeeded and self._started and len(self._batch) >= self._batch_size:
                self._flush_requested = True
                request_another = True
        if request_another and self._loop is not None:
            self._loop.call_soon(self._launch_immediate_flush)
        return succeeded

    def emit(self, record: logging.LogRecord) -> None:
        """
        处理日志记录。

        Args:
            record: 日志记录对象
        """
        try:
            # 提取日志信息
            entry = {
                "timestamp": datetime.fromtimestamp(record.created),
                "level": record.levelname,
                "logger": record.name,
                "message": self.format(record),
                "extra_data": getattr(record, "extra_data", None),
                "request_id": getattr(record, "request_id", None),
                "user_id": getattr(record, "user_id", None),
            }

            with self._batch_guard:
                if not self._accepting:
                    return
                self._batch.append(entry)
                should_flush = len(self._batch) >= self._batch_size
            if should_flush:
                self._request_flush()

        except Exception:
            self.handleError(record)
