"""WebSocket 端点鉴权测试。"""

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from unittest.mock import AsyncMock

from server.api import deps
from server.api.deps import set_token_verifier
from server.main import app


class FakeVerifier:
    """固定令牌校验器：good-token 有效，其余无效。"""

    def verify_token(self, token: str) -> tuple[str | None, str | None]:
        if token == "good-token":
            return "test_user", "test_session"
        return None, None


@pytest.fixture
def ws_verifier(monkeypatch):
    """临时注入固定校验器，用例结束后还原。"""
    original = deps._verifier
    set_token_verifier(FakeVerifier())
    monkeypatch.setattr(
        "server.domain.identity.session_service.session_service.get_active_session_username",
        AsyncMock(return_value="test_user"),
    )
    yield
    deps._verifier = original


def test_ws_rejects_missing_token(ws_verifier):
    """缺少 token 连接应被关闭（4401）。"""
    client = TestClient(app)
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
    assert exc.value.code == 4401


def test_ws_rejects_invalid_token(ws_verifier):
    """非法 token 连接应被关闭（4401）。"""
    client = TestClient(app)
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws?token=bad-token") as ws:
            ws.receive_json()
    assert exc.value.code == 4401


def test_ws_accepts_valid_token(ws_verifier):
    """合法 token 连接成功并收到 connected 消息。"""
    client = TestClient(app)
    with client.websocket_connect("/ws?token=good-token") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "connected"
        assert msg["client_id"]
