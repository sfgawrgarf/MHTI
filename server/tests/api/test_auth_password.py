"""Password-change session invalidation regressions."""

from unittest.mock import AsyncMock

import pytest

from server.api import auth as auth_api
from server.core.auth import AuthContext
from server.models.auth import ChangePasswordRequest


@pytest.mark.asyncio
async def test_password_change_revokes_every_other_session(monkeypatch) -> None:
    change_password = AsyncMock(return_value=(True, ["other-session"]))
    close_connections = AsyncMock()
    monkeypatch.setattr(auth_api.auth_service, "change_password", change_password)
    monkeypatch.setattr(
        auth_api.session_service,
        "close_session_connections",
        close_connections,
    )

    response = await auth_api.change_password(
        ChangePasswordRequest(
            current_password="old-password",
            new_password="new-password",
        ),
        AuthContext(username="admin", session_id="current-session"),
    )

    assert response.success is True
    change_password.assert_awaited_once_with(
        "admin",
        "old-password",
        "new-password",
        except_session_id="current-session",
    )
    close_connections.assert_awaited_once_with(["other-session"])


@pytest.mark.asyncio
async def test_session_revocation_is_scoped_to_current_user(monkeypatch) -> None:
    get_user_id = AsyncMock(return_value=7)
    revoke_session = AsyncMock(return_value=True)
    monkeypatch.setattr(auth_api.auth_service, "get_user_id", get_user_id)
    monkeypatch.setattr(auth_api.session_service, "revoke_session", revoke_session)

    response = await auth_api.revoke_session(
        "other-session",
        AuthContext(username="admin", session_id="current-session"),
    )

    assert response == {"message": "会话已注销"}
    get_user_id.assert_awaited_once_with("admin")
    revoke_session.assert_awaited_once_with("other-session", user_id=7)
