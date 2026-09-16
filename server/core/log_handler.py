"""自定义日志处理器 - 将日志写入数据库。"""

import asyncio
import logging
from datetime import datetime
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
        self._flush_task: asyncio.Task | None = None
        self._flush_lock = asyncio.Lock()
        self._started = False

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        """确保获取事件循环。"""
        try:
            return asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.get_event_loop()

    def start(self) -> None:
        """启动定时刷新任务。"""
        if self._started:
            return

        try:
            self._loop = self._ensure_loop()
            self._flush_task = self._loop.create_task(self._periodic_flush())
            self._started = True
        except RuntimeError:
            # 如果没有运行的事件循环，稍后再启动
            pass

    def stop(self) -> None:
        """停止处理器并刷新剩余日志。"""
        self._started = False
        if self._flush_task:
            self._flush_task.cancel()
            self._flush_task = None

        # 同步刷新剩余日志
        if self._batch and self._loop:
            try:
                if self._loop.is_running():
                    asyncio.create_task(self._flush())
                else:
                    self._loop.run_until_complete(self._flush())
            except Exception:
                pass

    async def _periodic_flush(self) -> None:
        """定时刷新日志到数据库。"""
        while self._started:
            try:
                await asyncio.sleep(self._flush_interval)
                await self._flush()
            except asyncio.CancelledError:
                break
            except Exception:
                pass

    async def _flush(self) -> None:
        """将缓冲的日志批量写入数据库。"""
        async with self._flush_lock:
            if not self._batch:
                return

            batch = self._batch.copy()
            self._batch.clear()

            try:
                await self._log_service.batch_insert(batch)
            except Exception as e:
                # New entries may have arrived while the database call was in
                # flight. Restore the failed batch in front of them so the next
                # periodic flush retries every record in its original order.
                self._batch[0:0] = batch
                print(f"Failed to flush logs to database: {e}")

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

            self._batch.append(entry)

            # 达到批量大小时异步刷新
            if len(self._batch) >= self._batch_size:
                if self._loop and self._loop.is_running():
                    asyncio.create_task(self._flush())

            # 如果还没启动，尝试启动
            if not self._started:
                self.start()

        except Exception:
            self.handleError(record)


class WebSocketLogHandler(logging.Handler):
    """
    将日志推送到 WebSocket 客户端的处理器。

    用于实时日志流功能。
    """

    def __init__(self, min_level: int = logging.INFO) -> None:
        """
        初始化 WebSocket 日志处理器。

        Args:
            min_level: 最低推送级别
        """
        super().__init__()
        self._min_level = min_level
        self._subscribers: set[str] = set()  # 订阅的客户端 ID
        self._ws_manager = None

    def set_ws_manager(self, manager: Any) -> None:
        """设置 WebSocket 连接管理器。"""
        self._ws_manager = manager

    def subscribe(self, client_id: str) -> None:
        """添加日志订阅者。"""
        self._subscribers.add(client_id)

    def unsubscribe(self, client_id: str) -> None:
        """移除日志订阅者。"""
        self._subscribers.discard(client_id)

    def emit(self, record: logging.LogRecord) -> None:
        """
        处理日志记录并推送到订阅者。

        Args:
            record: 日志记录对象
        """
        if record.levelno < self._min_level:
            return

        if not self._subscribers or not self._ws_manager:
            return

        try:
            message = {
                "type": "log",
                "data": {
                    "timestamp": datetime.fromtimestamp(record.created).isoformat(),
                    "level": record.levelname,
                    "logger": record.name,
                    "message": self.format(record),
                },
            }

            # 异步发送到所有订阅者
            try:
                loop = asyncio.get_running_loop()
                for client_id in self._subscribers.copy():
                    loop.create_task(
                        self._ws_manager.send_to_client(client_id, message)
                    )
            except RuntimeError:
                pass

        except Exception:
            self.handleError(record)


# 全局 WebSocket 日志处理器实例
_ws_log_handler: WebSocketLogHandler | None = None


def get_ws_log_handler() -> WebSocketLogHandler:
    """获取全局 WebSocket 日志处理器。"""
    global _ws_log_handler
    if _ws_log_handler is None:
        _ws_log_handler = WebSocketLogHandler()
    return _ws_log_handler
