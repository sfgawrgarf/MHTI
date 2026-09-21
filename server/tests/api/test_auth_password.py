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
