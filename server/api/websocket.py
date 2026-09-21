"""WebSocket API 路由"""

import asyncio
import logging
import uuid
from collections.abc import Callable

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from server.core.auth import authenticate_access_token
from server.services.websocket_manager import ConnectionManager, get_ws_manager

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/ws", tags=["websocket"])

# 心跳配置
HEARTBEAT_INTERVAL = 30  # 心跳间隔（秒）
CLIENT_TIMEOUT = 90  # 客户端超时时间（秒）
AUTH_TIMEOUT = 5
MAX_JOB_IDS_PER_MESSAGE = 100


def _parse_job_ids(message: object) -> list[str]:
    """Return a bounded, de-duplicated list of valid subscription IDs."""
    if not isinstance(message, dict):
        return []
    raw_job_ids = message.get("job_ids")
    if not isinstance(raw_job_ids, list):
        return []

    job_ids: list[str] = []
    for raw_job_id in raw_job_ids:
        if not isinstance(raw_job_id, (str, int)):
            continue
        job_id = str(raw_job_id).strip()
        if not job_id or len(job_id) > 128 or job_id in job_ids:
            continue
        job_ids.append(job_id)
        if len(job_ids) >= MAX_JOB_IDS_PER_MESSAGE:
            break
    return job_ids


@router.websocket("")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket 连接端点

    优化：
    - 添加心跳检测，确保连接活跃
    - 使用 try-except 包装所有发送操作，避免单次错误导致连接断开
    """
    manager = get_ws_manager()
    client_id = str(uuid.uuid4())[:8]

    await websocket.accept()
    try:
        auth_message = await asyncio.wait_for(
            websocket.receive_json(),
            timeout=AUTH_TIMEOUT,
        )
    except WebSocketDisconnect:
        return
    except (asyncio.TimeoutError, ValueError):
        await websocket.close(code=4401, reason="Authentication required")
        return

    if not isinstance(auth_message, dict) or auth_message.get("type") != "auth":
        await websocket.close(code=4401, reason="Authentication required")
        return
    auth = await authenticate_access_token(str(auth_message.get("token") or ""))
    if auth is None:
        await websocket.close(code=4401, reason="Invalid or revoked token")
        return

    manager.connect(client_id, websocket, auth.session_id)

    # 创建心跳任务和超时检测任务
    heartbeat_task = None
    timeout_task = None

    try:
        # 发送连接成功消息
        connected = await manager.send_to_client(client_id, {
            "type": "connected",
            "client_id": client_id,
        })
        if not connected:
            return

        # 启动心跳任务（服务端定时发送 ping）
        heartbeat_task = asyncio.create_task(_heartbeat_loop(manager, client_id))

        # 启动超时检测任务
        loop = asyncio.get_running_loop()
        last_activity_time = loop.time()
        timeout_task = asyncio.create_task(
            _timeout_monitor(
                client_id,
                manager,
                lambda: last_activity_time,
                session_id=auth.session_id,
                username=auth.username,
            )
        )

        while True:
            data = await websocket.receive_json()
            if not isinstance(data, dict):
                continue
            msg_type = data.get("type")

            if msg_type == "ping":
                # 客户端心跳响应
                last_activity_time = loop.time()
                # 响应 pong
                if not await manager.send_to_client(client_id, {"type": "pong"}):
                    break

            elif msg_type == "subscribe":
                # 订阅任务进度
                job_ids = manager.subscribe(client_id, _parse_job_ids(data))
                if not await manager.send_to_client(
                    client_id,
                    {"type": "subscribed", "job_ids": job_ids},
                ):
                    break

            elif msg_type == "unsubscribe":
                # 取消订阅
                job_ids = _parse_job_ids(data)
                manager.unsubscribe(client_id, job_ids)

    except WebSocketDisconnect:
        logger.info(f"[{client_id}] WebSocket 断开连接")
    except asyncio.CancelledError:
        logger.info(f"[{client_id}] WebSocket 任务被取消")
    except Exception as e:
        logger.error(f"[{client_id}] WebSocket 错误: {e}")
    finally:
        # 清理任务和连接
        if heartbeat_task:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass
        if timeout_task:
            timeout_task.cancel()
            try:
                await timeout_task
            except asyncio.CancelledError:
                pass
        manager.disconnect(client_id)


async def _heartbeat_loop(manager: ConnectionManager, client_id: str):
    """服务端心跳发送循环

    定期向客户端发送 ping，保持连接活跃
    """
    try:
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            sent = await manager.send_to_client(
                client_id,
                {"type": "ping", "timestamp": asyncio.get_running_loop().time()},
            )
            if not sent:
                break
    except asyncio.CancelledError:
        pass


async def _timeout_monitor(
    client_id: str,
    manager: ConnectionManager,
    get_last_activity_time: Callable[[], float],
    *,
    session_id: str | None = None,
    username: str | None = None,
):
    """客户端超时检测

    检测客户端是否在规定时间内响应心跳，
    如果超时则主动断开连接
    """
    try:
        while True:
            await asyncio.sleep(10)  # 每10秒检查一次
            if session_id:
                from server.services.session_service import session_service

                try:
                    session_active = await session_service.is_session_active(session_id)
                except Exception:
                    logger.exception("[%s] 无法重新验证 WebSocket 会话", client_id)
                    await manager.close_client(
                        client_id,
                        code=1011,
                        reason="Session validation failed",
                    )
                    break
                if not session_active:
                    logger.info("[%s] 会话已失效，关闭 WebSocket", client_id)
                    await manager.close_client(
                        client_id,
                        code=4401,
                        reason="Session revoked",
                    )
                    break
            current_time = asyncio.get_running_loop().time()
            if current_time - get_last_activity_time() > CLIENT_TIMEOUT:
                logger.warning(f"[{client_id}] 客户端超时（{CLIENT_TIMEOUT}s 无响应），主动断开")
                await manager.close_client(
                    client_id,
                    code=1001,
                    reason="Client heartbeat timeout",
                )
                break
    except asyncio.CancelledError:
        pass
