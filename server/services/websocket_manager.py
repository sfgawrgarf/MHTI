"""WebSocket 连接管理器 - 实时推送刮削进度"""

import asyncio
import logging
from datetime import datetime
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    """WebSocket 连接管理器"""

    def __init__(
        self,
        *,
        send_timeout: float = 5.0,
        max_subscriptions_per_client: int = 500,
    ) -> None:
        # client_id -> WebSocket
        self.active_connections: dict[str, WebSocket] = {}
        # client_id -> authenticated session_id
        self.client_sessions: dict[str, str] = {}
        # job_id -> set of client_ids (订阅关系)
        self.subscriptions: dict[str, set[str]] = {}
        # Starlette WebSocket writes must be serialized per connection.
        self._send_locks: dict[str, asyncio.Lock] = {}
        self._send_timeout = send_timeout
        self._max_subscriptions_per_client = max_subscriptions_per_client

    def connect(self, client_id: str, websocket: WebSocket, session_id: str) -> None:
        """Register a connection only after the endpoint authenticated it."""
        self.active_connections[client_id] = websocket
        self.client_sessions[client_id] = session_id
        self._send_locks[client_id] = asyncio.Lock()
        logger.info(f"WebSocket 连接: {client_id}")

    def disconnect(self, client_id: str) -> None:
        """断开连接"""
        was_connected = self.active_connections.pop(client_id, None) is not None
        self.client_sessions.pop(client_id, None)
        self._send_locks.pop(client_id, None)
        # 清理订阅关系
        for job_id in list(self.subscriptions.keys()):
            self.subscriptions[job_id].discard(client_id)
            if not self.subscriptions[job_id]:
                del self.subscriptions[job_id]
        if was_connected:
            logger.info(f"WebSocket 断开: {client_id}")

    def subscribe(self, client_id: str, job_ids: list[str]) -> list[str]:
        """订阅任务进度"""
        if client_id not in self.active_connections:
            return []

        subscribed_count = sum(
            client_id in subscribers for subscribers in self.subscriptions.values()
        )
        accepted: list[str] = []
        for job_id in dict.fromkeys(job_ids):
            subscribers = self.subscriptions.get(job_id)
            if subscribers is not None and client_id in subscribers:
                accepted.append(job_id)
                continue
            if subscribed_count >= self._max_subscriptions_per_client:
                break
            if job_id not in self.subscriptions:
                self.subscriptions[job_id] = set()
            self.subscriptions[job_id].add(client_id)
            subscribed_count += 1
            accepted.append(job_id)
        logger.debug(f"客户端 {client_id} 订阅任务: {accepted}")
        return accepted

    def unsubscribe(self, client_id: str, job_ids: list[str]) -> None:
        """取消订阅"""
        for job_id in job_ids:
            if job_id in self.subscriptions:
                self.subscriptions[job_id].discard(client_id)
                if not self.subscriptions[job_id]:
                    del self.subscriptions[job_id]

    async def send_to_client(self, client_id: str, message: dict[str, Any]) -> bool:
        """发送消息给指定客户端"""
        websocket = self.active_connections.get(client_id)
        send_lock = self._send_locks.get(client_id)
        if websocket is None or send_lock is None:
            return False
        # Keep the narrowed types explicit for incremental mypy runs where the
        # FastAPI import is intentionally skipped and therefore becomes Any.
        assert websocket is not None
        assert send_lock is not None
        try:
            async with send_lock:
                # The connection may have been removed while this send waited.
                if self.active_connections.get(client_id) is not websocket:
                    return False
                await asyncio.wait_for(
                    websocket.send_json(message),
                    timeout=self._send_timeout,
                )
                return True
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"发送消息失败 {client_id}: {e}")
            self.disconnect(client_id)
            try:
                await asyncio.wait_for(
                    websocket.close(code=1011, reason="WebSocket send failed"),
                    timeout=self._send_timeout,
                )
            except Exception:
                pass
            return False

    async def broadcast_to_job(self, job_id: str, message: dict[str, Any]) -> None:
        """向订阅了该任务的所有客户端广播消息"""
        client_ids = self.subscriptions.get(job_id, set()).copy()
        if client_ids:
            await asyncio.gather(
                *(self.send_to_client(client_id, message) for client_id in client_ids)
            )

    async def broadcast_all(self, message: dict[str, Any]) -> None:
        """向所有连接的客户端广播消息"""
        client_ids = list(self.active_connections.keys())
        if client_ids:
            await asyncio.gather(
                *(self.send_to_client(client_id, message) for client_id in client_ids)
            )

# 全局单例
_manager: ConnectionManager | None = None


def get_ws_manager() -> ConnectionManager:
    """获取 WebSocket 管理器单例"""
    global _manager
    if _manager is None:
        _manager = ConnectionManager()
    return _manager


class ProgressNotifier:
    """刮削进度通知器"""

    def __init__(self, manager: ConnectionManager | None = None):
        self.manager = manager or get_ws_manager()

    def _make_message(
        self, msg_type: str, job_id: str | None, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """构造消息"""
        msg = {
            "type": msg_type,
            "payload": payload,
            "timestamp": datetime.now().isoformat(),
        }
        if job_id:
            msg["job_id"] = job_id
        return msg

    async def notify_job_created(self, job_id: str, file_path: str, status: str) -> None:
        """通知任务已创建"""
        msg = self._make_message("job_created", job_id, {
            "file_path": file_path,
            "status": status,
        })
        await self.manager.broadcast_all(msg)

    async def notify_progress(
        self, job_id: str, step: str, progress: int, message: str
    ) -> None:
        """通知进度更新"""
        msg = self._make_message("job_progress", job_id, {
            "step": step,
            "progress": progress,
            "message": message,
        })
        await self.manager.broadcast_to_job(job_id, msg)

    async def notify_log(self, job_id: str, level: str, message: str) -> None:
        """通知日志消息"""
        msg = self._make_message("log", job_id, {
            "level": level,
            "message": message,
        })
        await self.manager.broadcast_to_job(job_id, msg)

    async def notify_completed(self, job_id: str, result: dict[str, Any]) -> None:
        """通知任务完成"""
        msg = self._make_message("job_completed", job_id, result)
        await self.manager.broadcast_to_job(job_id, msg)

    async def notify_failed(self, job_id: str, error: str) -> None:
        """通知任务失败"""
        msg = self._make_message("job_failed", job_id, {"error": error})
        await self.manager.broadcast_to_job(job_id, msg)

    async def notify_cancelled(self, job_id: str, message: str) -> None:
        """通知任务已完成安全取消收尾。"""
        msg = self._make_message("job_cancelled", job_id, {
            "status": "cancelled",
            "message": message,
        })
        await self.manager.broadcast_to_job(job_id, msg)

    async def notify_need_action(
        self,
        job_id: str,
        action_type: str,
        options: list[Any] | dict[str, Any],
    ) -> None:
        """通知需要用户操作"""
        msg = self._make_message("need_action", job_id, {
            "action_type": action_type,
            "options": options,
        })
        await self.manager.broadcast_to_job(job_id, msg)

    # ========== 历史记录通知 ==========

    async def notify_history_created(self, record: dict[str, Any]) -> None:
        """通知新历史记录创建"""
        msg = self._make_message("history_created", None, record)
        await self.manager.broadcast_all(msg)

    async def notify_history_updated(
        self, record_id: str, updates: dict[str, Any]
    ) -> None:
        """通知历史记录更新"""
        msg = self._make_message("history_updated", None, {"id": record_id, **updates})
        await self.manager.broadcast_all(msg)

    async def notify_history_deleted(self, record_id: str) -> None:
        """通知历史记录删除"""
        msg = self._make_message("history_deleted", None, {"id": record_id})
        await self.manager.broadcast_all(msg)

    async def notify_history_cleared(self, count: int) -> None:
        """通知历史记录清空"""
        msg = self._make_message("history_cleared", None, {"count": count})
        await self.manager.broadcast_all(msg)

    # ========== 历史记录详情页实时更新 ==========

    async def notify_history_detail_update(
        self, record_id: str, updates: dict[str, Any]
    ) -> None:
        """通知历史记录详情更新（用于详情页实时刷新）

        此方法向订阅了特定 record_id 的客户端发送增量更新。
        前端使用 subscribe([recordId]) 订阅，使用 unsubscribe([recordId]) 取消订阅。

        Args:
            record_id: 历史记录 ID
            updates: 更新内容，可包含:
                - status: 状态变化 (running/success/failed/etc.)
                - progress: 进度百分比
                - logs: 刮削日志列表
                - 其他需要更新的字段
        """
        msg = self._make_message("history_detail_update", record_id, updates)
        await self.manager.broadcast_to_job(record_id, msg)

    async def notify_history_detail_log(
        self, record_id: str, log_step: dict[str, Any]
    ) -> None:
        """通知历史记录详情页新增日志步骤

        Args:
            record_id: 历史记录 ID
            log_step: 日志步骤，格式: {"name": str, "completed": bool, "logs": list}
        """
        msg = self._make_message("history_detail_log", record_id, log_step)
        await self.manager.broadcast_to_job(record_id, msg)


def get_notifier() -> ProgressNotifier:
    """获取进度通知器"""
    return ProgressNotifier(get_ws_manager())
