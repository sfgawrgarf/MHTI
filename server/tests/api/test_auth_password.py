"""Password-change session invalidation regressions."""

from unittest.mock import AsyncMock

import pytest

from server.api import auth as auth_api
from server.core.auth import AuthContext
from server.models.auth import ChangePasswordRequest


@pytest.mark.asyncio
async def test_password_change_revokes_every_other_session(monkeypatch) -> None:
    change_password = AsyncMock(return_value=True)
    get_user_id = AsyncMock(return_value=7)
    revoke_all = AsyncMock(return_value=2)
    monkeypatch.setattr(auth_api.auth_service, "change_password", change_password)
    monkeypatch.setattr(auth_api.auth_service, "get_user_id", get_user_id)
    monkeypatch.setattr(auth_api.session_service, "revoke_all_sessions", revoke_all)

    response = await auth_api.change_password(
        ChangePasswordRequest(
            current_password="old-password",
            new_password="new-password",
        ),
        AuthContext(username="admin", session_id="current-session"),
    )

    assert response.success is True
    revoke_all.assert_awaited_once_with(7, except_session_id="current-session")
