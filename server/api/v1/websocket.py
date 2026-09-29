"""WebSocket API 路由"""

import asyncio
import json
import logging
import uuid
from json import JSONDecodeError

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from server.api.deps import authenticate_access_token
from server.common.limits import (
    MAX_WS_ACTION_TYPE_LENGTH,
    MAX_WS_JOB_ID_LENGTH,
    MAX_WS_JOB_IDS_PER_MESSAGE,
    MAX_WS_MESSAGE_BYTES,
    MAX_WS_MESSAGE_TYPE_LENGTH,
)
from server.infrastructure.realtime import get_ws_manager

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/ws", tags=["websocket"])

# 心跳配置
HEARTBEAT_INTERVAL = 30  # 心跳间隔（秒）
CLIENT_TIMEOUT = 90  # 客户端超时时间（秒）


@router.websocket("")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket 连接端点

    优化：
    - 添加心跳检测，确保连接活跃
    - 使用 try-except 包装所有发送操作，避免单次错误导致连接断开
    """
    # 查询参数鉴权（浏览器 WebSocket 无法携带 Authorization 头）
    token = websocket.query_params.get("token")
    if not token or len(token) > 4096:
        await websocket.close(code=4401)
        return

    auth = await authenticate_access_token(token)
    if auth is None:
        await websocket.close(code=4401)
        return

    manager = get_ws_manager()
    client_id = str(uuid.uuid4())

    await manager.connect(client_id, websocket, auth.session_id)

    # 创建心跳任务和超时检测任务
    heartbeat_task = None
    timeout_task = None

    try:
        # 发送连接成功消息
        if not await manager.send_to_client(client_id, {
            "type": "connected",
            "client_id": client_id,
        }):
            return

        # 启动心跳任务（服务端定时发送 ping）
        heartbeat_task = asyncio.create_task(
            _heartbeat_loop(websocket, client_id, manager)
        )

        # 启动超时检测任务
        last_pong_time = asyncio.get_event_loop().time()
        timeout_task = asyncio.create_task(
            _timeout_monitor(
                websocket,
                client_id,
                manager,
                last_pong_time,
                session_id=auth.session_id,
                username=auth.username,
            )
        )

        while True:
            raw_message = await websocket.receive_text()
            if len(raw_message.encode("utf-8")) > MAX_WS_MESSAGE_BYTES:
                await manager.close_client(
                    client_id,
                    code=1009,
                    reason="WebSocket message too large",
                )
                return
            try:
                data = json.loads(raw_message)
            except (JSONDecodeError, TypeError):
                await manager.close_client(
                    client_id,
                    code=1003,
                    reason="Invalid WebSocket message",
                )
                return
            if not isinstance(data, dict):
                await manager.close_client(
                    client_id,
                    code=1003,
                    reason="Invalid WebSocket message",
                )
                return
            msg_type = data.get("type")
            if not isinstance(msg_type, str) or len(msg_type) > MAX_WS_MESSAGE_TYPE_LENGTH:
                await manager.send_to_client(client_id, {
                    "type": "error",
                    "code": "invalid_message_type",
                })
                continue

            if msg_type == "ping":
                # 客户端心跳响应
                last_pong_time = asyncio.get_event_loop().time()
                # 更新超时任务
                if timeout_task:
                    timeout_task.cancel()
                    timeout_task = asyncio.create_task(
                        _timeout_monitor(
                            websocket,
                            client_id,
                            manager,
                            last_pong_time,
                            session_id=auth.session_id,
                            username=auth.username,
                        )
                    )
                # 响应 pong
                try:
                    await manager.send_to_client(client_id, {"type": "pong"})
                except Exception as e:
                    logger.warning(f"[{client_id}] 发送 pong 失败: {e}")

            elif msg_type == "subscribe":
                # 订阅任务进度
                job_ids = data.get("job_ids", [])
                if (
                    not isinstance(job_ids, list)
                    or len(job_ids) > MAX_WS_JOB_IDS_PER_MESSAGE
                ):
                    await manager.send_to_client(client_id, {
                        "type": "error",
                        "code": "too_many_job_ids",
                    })
                    continue
                accepted_job_ids = manager.subscribe(
                    client_id, job_ids
                )
                try:
                    await manager.send_to_client(client_id, {
                        "type": "subscribed",
                        "job_ids": accepted_job_ids,
                    })
                except Exception as e:
                    logger.warning(f"[{client_id}] 发送订阅确认失败: {e}")

            elif msg_type == "unsubscribe":
                # 取消订阅
                job_ids = data.get("job_ids", [])
                if (
                    not isinstance(job_ids, list)
                    or len(job_ids) > MAX_WS_JOB_IDS_PER_MESSAGE
                ):
                    await manager.send_to_client(client_id, {
                        "type": "error",
                        "code": "too_many_job_ids",
                    })
                    continue
                manager.unsubscribe(client_id, job_ids)

            elif msg_type == "user_action":
                # 用户响应（选择匹配结果等）
                job_id = data.get("job_id")
                action_type = data.get("action_type")
                selection = data.get("selection")
                if (
                    not isinstance(job_id, str)
                    or not job_id.strip()
                    or len(job_id.strip()) > MAX_WS_JOB_ID_LENGTH
                    or not manager.is_subscribed(client_id, job_id)
                ):
                    await manager.send_to_client(client_id, {
                        "type": "error",
                        "code": "action_not_authorized",
                    })
                    continue
                if (
                    not isinstance(action_type, str)
                    or not action_type.strip()
                    or len(action_type) > MAX_WS_ACTION_TYPE_LENGTH
                ):
                    await manager.send_to_client(client_id, {
                        "type": "error",
                        "code": "invalid_action_type",
                    })
                    continue
                manager.resolve_action(
                    job_id.strip(),
                    {
                        "action_type": action_type.strip(),
                        "selection": selection,
                    },
                    client_id=client_id,
                )

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


async def _heartbeat_loop(
    websocket: WebSocket,
    client_id: str,
    manager=None,
):
    """服务端心跳发送循环

    定期向客户端发送 ping，保持连接活跃
    """
    try:
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            try:
                message = {
                    "type": "ping",
                    "timestamp": asyncio.get_event_loop().time(),
                }
                if manager is None:
                    await websocket.send_json(message)
                else:
                    await manager.send_to_client(client_id, message)
            except Exception as e:
                logger.warning(f"[{client_id}] 发送心跳失败: {e}")
                break
    except asyncio.CancelledError:
        pass


async def _timeout_monitor(
    websocket: WebSocket,
    client_id: str,
    manager,
    last_pong_time: float,
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
                from server.domain.identity.session_service import session_service

                try:
                    session_active = await session_service.is_session_active(
                        session_id, username
                    )
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
            current_time = asyncio.get_event_loop().time()
            if current_time - last_pong_time > CLIENT_TIMEOUT:
                logger.warning(f"[{client_id}] 客户端超时（{CLIENT_TIMEOUT}s 无响应），主动断开")
                try:
                    await manager.close_client(
                        client_id,
                        code=1001,
                        reason="Client heartbeat timeout",
                    )
                except Exception:
                    pass
                break
    except asyncio.CancelledError:
        pass
