"""Runtime application of the persisted logging configuration."""

import asyncio
import logging
from collections import deque
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from threading import Lock
from typing import TYPE_CHECKING, Any

from server.core.log_handler import DatabaseLogHandler
from server.models.log import LogConfig

if TYPE_CHECKING:
    from server.services.log_service import LogService


LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
LOG_DIR = Path(__file__).parent.parent.parent / "data" / "logs"


class RealtimeLogHandler(logging.Handler):
    """Forward a bounded stream of application logs to WebSocket clients."""

    def __init__(self, *, max_queue_size: int = 1000) -> None:
        super().__init__()
        self._max_queue_size = max_queue_size
        self._queue: asyncio.Queue[dict[str, Any]] | None = None
        self._worker: asyncio.Task[None] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._accepting = False
        self._pending: deque[dict[str, Any]] = deque(maxlen=max_queue_size)
        self._pending_guard = Lock()
        self._drain_scheduled = False

    def start(self) -> None:
        if self._worker is not None:
            return
        self._loop = asyncio.get_running_loop()
        self._queue = asyncio.Queue(maxsize=self._max_queue_size)
        self._accepting = True
        self._worker = self._loop.create_task(self._run())

    def emit(self, record: logging.LogRecord) -> None:
        if not self._accepting or self._loop is None:
            return
        # Delivery failures are logged by this module; forwarding those records
        # again would create a feedback loop.
        if record.name == "server.services.websocket_manager":
            return
        message = {
            "type": "system_log",
            "payload": {
                "level": record.levelname,
                "logger": record.name,
                "message": self.format(record),
            },
            "timestamp": datetime.fromtimestamp(record.created).isoformat(),
        }
        schedule_drain = False
        with self._pending_guard:
            self._pending.append(message)
            if not self._drain_scheduled:
                self._drain_scheduled = True
                schedule_drain = True
        if schedule_drain:
            self._loop.call_soon_threadsafe(self._drain_pending)

    def _drain_pending(self) -> None:
        queue = self._queue
        if not self._accepting or queue is None:
            return
        with self._pending_guard:
            pending = list(self._pending)
            self._pending.clear()
            self._drain_scheduled = False
        for message in pending:
            if queue.full():
                try:
                    queue.get_nowait()
                    queue.task_done()
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(message)
        with self._pending_guard:
            if self._pending and not self._drain_scheduled:
                self._drain_scheduled = True
                self._loop.call_soon(self._drain_pending)

    async def _run(self) -> None:
        from server.services.websocket_manager import get_ws_manager

        queue = self._queue
        if queue is None:
            return
        try:
            while True:
                message = await queue.get()
                try:
                    await get_ws_manager().broadcast_all(message)
                finally:
                    queue.task_done()
        except asyncio.CancelledError:
            pass

    async def aclose(self) -> None:
        self._accepting = False
        if self._worker is not None:
            self._worker.cancel()
            try:
                await self._worker
            except asyncio.CancelledError:
                pass
        self._worker = None
        self._queue = None
        self._loop = None
        with self._pending_guard:
            self._pending.clear()
            self._drain_scheduled = False


class LoggingRuntime:
    """Own and reconfigure handlers created by MHTI."""

    def __init__(self) -> None:
        self.root = logging.getLogger()
        self.console_handler = logging.StreamHandler()
        self.console_handler.set_name("mhti-console")
        self.console_handler.setFormatter(logging.Formatter(LOG_FORMAT))
        self.file_handler: RotatingFileHandler | None = None
        self.database_handler: DatabaseLogHandler | None = None
        self.realtime_handler: RealtimeLogHandler | None = None
        self._file_signature: tuple[int, int, int] | None = None
        self._apply_lock = asyncio.Lock()

    def bootstrap(self) -> None:
        """Provide startup logging until the persisted configuration is loaded."""
        self.root.setLevel(logging.INFO)
        self.console_handler.setLevel(logging.INFO)
        if self.console_handler not in self.root.handlers:
            self.root.addHandler(self.console_handler)

    async def apply(self, config: LogConfig, log_service: "LogService") -> None:
        """Apply a persisted configuration immediately."""
        async with self._apply_lock:
            level = getattr(logging, config.log_level.value)
            self.root.setLevel(level)
            self.console_handler.setLevel(level)
            if config.console_enabled:
                if self.console_handler not in self.root.handlers:
                    self.root.addHandler(self.console_handler)
            elif self.console_handler in self.root.handlers:
                self.root.removeHandler(self.console_handler)

            await self._configure_file_handler(config, level)
            await self._configure_database_handler(config, log_service, level)
            await self._configure_realtime_handler(config, level)

    async def _configure_file_handler(self, config: LogConfig, level: int) -> None:
        signature = (config.max_file_size_mb, config.max_file_count, level)
        if not config.file_enabled:
            self._close_file_handler()
            return
        if self.file_handler is not None and self._file_signature == signature:
            return

        self._close_file_handler()
        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            handler = RotatingFileHandler(
                LOG_DIR / "app.log",
                maxBytes=config.max_file_size_mb * 1024 * 1024,
                backupCount=config.max_file_count,
                encoding="utf-8",
            )
            handler.set_name("mhti-file")
            handler.setLevel(level)
            handler.setFormatter(logging.Formatter(LOG_FORMAT))
            self.root.addHandler(handler)
            self.file_handler = handler
            self._file_signature = signature
        except Exception:
            logging.getLogger(__name__).exception("Unable to configure file logging")

    def _close_file_handler(self) -> None:
        if self.file_handler is not None:
            self.root.removeHandler(self.file_handler)
            self.file_handler.close()
        self.file_handler = None
        self._file_signature = None

    async def _configure_database_handler(
        self,
        config: LogConfig,
        log_service: "LogService",
        level: int,
    ) -> None:
        if not config.db_enabled:
            await self._close_database_handler()
            return
        if self.database_handler is None:
            handler = DatabaseLogHandler(
                log_service,
                batch_size=50,
                flush_interval=10.0,
                max_buffer_size=5000,
                write_timeout=3.0,
            )
            handler.set_name("mhti-database")
            handler.setFormatter(logging.Formatter(LOG_FORMAT))
            self.root.addHandler(handler)
            handler.start()
            self.database_handler = handler
        self.database_handler.setLevel(level)

    async def _close_database_handler(self) -> bool:
        handler = self.database_handler
        if handler is None:
            return True
        self.root.removeHandler(handler)
        self.database_handler = None
        return await handler.aclose()

    async def _configure_realtime_handler(self, config: LogConfig, level: int) -> None:
        if not config.realtime_enabled:
            await self._close_realtime_handler()
            return
        if self.realtime_handler is None:
            handler = RealtimeLogHandler()
            handler.set_name("mhti-realtime")
            handler.setFormatter(logging.Formatter(LOG_FORMAT))
            self.root.addHandler(handler)
            handler.start()
            self.realtime_handler = handler
        self.realtime_handler.setLevel(level)

    async def _close_realtime_handler(self) -> None:
        handler = self.realtime_handler
        if handler is None:
            return
        self.root.removeHandler(handler)
        self.realtime_handler = None
        await handler.aclose()

    async def shutdown(self) -> bool:
        """Detach owned handlers and make a bounded final database flush."""
        async with self._apply_lock:
            await self._close_realtime_handler()
            logs_flushed = await self._close_database_handler()
            self._close_file_handler()
            return logs_flushed


_runtime = LoggingRuntime()


def get_logging_runtime() -> LoggingRuntime:
    return _runtime
