"""Regression tests for authentication and file-operation boundaries."""

from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request
from starlette.websockets import WebSocketDisconnect

from server.core.auth import AuthContext, authenticate_access_token, get_client_ip
from server.core.path_security import (
    PathSecurityError,
    validate_image_url,
    validate_media_path,
)
from server.main import app
from server.models.image import ImageDownloadRequest
from server.models.subtitle import SubtitleRenameRequest


def test_file_operation_routes_require_authentication(client: TestClient) -> None:
    """Previously public read/write helpers must now reject anonymous callers."""
    assert client.get("/api/templates/default").status_code == 401
    assert client.post(
        "/api/rename/preview",
        json={
            "source_path": "/tmp/video.mp4",
            "title": "Show",
            "season": 1,
            "episode": 1,
        },
    ).status_code == 401
    assert client.post(
        "/api/images/download",
        json={
            "url": "https://image.tmdb.org/t/p/w500/poster.jpg",
            "save_path": "/tmp",
            "filename": "poster.jpg",
        },
    ).status_code == 401


def test_frontend_config_remains_public_and_reports_release_version(
    client: TestClient,
) -> None:
    """The login page can read runtime config without reopening private APIs."""
    response = client.get("/api/config/frontend")
    assert response.status_code == 200
    assert response.json()["version"] == "2.0.3"


@pytest.mark.asyncio
async def test_access_token_requires_active_session(monkeypatch: pytest.MonkeyPatch) -> None:
    """A valid JWT is rejected immediately after its session is revoked."""
    monkeypatch.setattr(
        "server.core.auth.auth_service.verify_token",
        lambda _token: ("admin", "revoked-session"),
    )
    active_check = AsyncMock(return_value=False)
    monkeypatch.setattr(
        "server.core.auth.session_service.is_session_active",
        active_check,
    )

    assert await authenticate_access_token("signed-token") is None
    active_check.assert_awaited_once_with("revoked-session", "admin")


def test_websocket_requires_authentication(client: TestClient) -> None:
    """The socket accepts no subscriptions before a successful auth message."""
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/ws") as websocket:
            websocket.send_json({"type": "subscribe", "job_ids": ["secret-job"]})
            websocket.receive_json()
    assert exc_info.value.code == 4401


def test_websocket_accepts_active_session(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An authenticated socket receives its connection acknowledgement."""
    auth_check = AsyncMock(return_value=AuthContext("admin", "session-1"))
    monkeypatch.setattr(
        "server.api.websocket.authenticate_access_token",
        auth_check,
    )

    with client.websocket_connect("/ws") as websocket:
        websocket.send_json({"type": "auth", "token": "valid-token"})
        message = websocket.receive_json()
        assert message["type"] == "connected"
    auth_check.assert_awaited_once_with("valid-token")


def test_file_paths_stay_inside_configured_roots(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Allowed roots work while system paths remain inaccessible."""
    monkeypatch.setenv("MHTI_ALLOWED_MEDIA_ROOTS", str(tmp_path))
    allowed = tmp_path / "poster.jpg"
    assert validate_media_path(str(allowed)) == allowed.resolve()
    with pytest.raises(PathSecurityError):
        validate_media_path("/etc/passwd", must_exist=True)


def test_image_and_subtitle_models_reject_path_components() -> None:
    """Filenames cannot escape their supplied destination directory."""
    with pytest.raises(ValueError):
        ImageDownloadRequest(
            url="https://image.tmdb.org/t/p/w500/poster.jpg",
            save_path="/tmp",
            filename="../secret",
        )
    with pytest.raises(ValueError):
        SubtitleRenameRequest(
            subtitle_path="/tmp/a.srt",
            new_video_name="../moved",
        )


def test_image_download_hosts_are_allowlisted(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remote downloads cannot target arbitrary or local services."""
    monkeypatch.setenv("MHTI_ALLOWED_IMAGE_HOSTS", "image.tmdb.org")
    assert validate_image_url(
        "https://image.tmdb.org/t/p/w500/poster.jpg"
    ).startswith("https://")
    with pytest.raises(PathSecurityError):
        validate_image_url("http://127.0.0.1/admin")
    with pytest.raises(PathSecurityError):
        validate_image_url("https://example.com/image.jpg")


def test_forwarded_ip_uses_trusted_edge_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Attacker-controlled leftmost XFF values do not bypass rate limiting."""
    monkeypatch.setenv("MHTI_TRUSTED_PROXY_NETWORKS", "127.0.0.0/8")
    monkeypatch.setenv("MHTI_TRUSTED_PROXY_HOPS", "1")
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/auth/login",
            "headers": [
                (b"x-forwarded-for", b"198.51.100.99, 203.0.113.20"),
            ],
            "client": ("127.0.0.1", 12345),
        }
    )
    assert get_client_ip(request) == "203.0.113.20"
