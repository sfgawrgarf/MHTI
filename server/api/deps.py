"""Authentication dependencies for API protection."""

from typing import Protocol

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from server.common.http import get_client_ip  # noqa: F401  统一依赖入口

# ---- 容器装配转发（routers 统一从本模块导入依赖 getter）----
from server.bootstrap import (  # noqa: E402,F401
    get_auth_config_service,
    get_config_service,
    get_emby_service,
    get_file_service,
    get_history_service,
    get_image_service,
    get_job_monitor_service,
    get_log_service,
    get_manual_job_service,
    get_nfo_service,
    get_parser_service,
    get_p115_service,
    get_rename_service,
    get_scrape_job_service,
    get_scraped_file_service,
    get_scraper_service,
    get_scheduler_service,
    get_subtitle_service,
    get_template_service,
    get_tmdb_service,
    get_watcher_service,
    get_websocket_manager,
)


class TokenVerifier(Protocol):
    """Token 校验端口 - 实现由组合根注入。"""

    def verify_token(self, token: str) -> tuple[str | None, str | None]: ...


_verifier: TokenVerifier | None = None


def set_token_verifier(verifier: TokenVerifier) -> None:
    """注入 Token 校验实现（由组合根在启动时调用）。"""
    global _verifier
    _verifier = verifier


def _get_verifier() -> TokenVerifier:
    if _verifier is None:
        raise RuntimeError("Token 验证器未注入，请确认应用通过 lifespan 启动")
    return _verifier


def verify_token(token: str | None) -> tuple[str | None, str | None]:
    """校验访问令牌，返回 (username, session_id)。

    供 WebSocket 查询参数鉴权等无法携带 HTTP 头的场景复用。
    """
    if not token:
        return None, None
    return _get_verifier().verify_token(token)


async def authenticate_access_token(token: str) -> AuthContext | None:
    """Validate a JWT and confirm that its backing session is still active."""
    from server.domain.identity.session_service import session_service

    token_username, session_id = _get_verifier().verify_token(token)
    if not token_username or not session_id:
        return None
    username = await session_service.get_active_session_username(session_id)
    if username is None:
        return None
    return AuthContext(username=username, session_id=session_id)


security = HTTPBearer(auto_error=False)


class AuthContext:
    """Authentication context with user info."""

    def __init__(self, username: str, session_id: str):
        self.username = username
        self.session_id = session_id


async def require_auth(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> AuthContext:
    """
    Dependency that requires valid authentication.

    Returns:
        AuthContext with username and session_id.

    Raises:
        HTTPException: 401 if not authenticated.
    """
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未提供认证信息",
            headers={"WWW-Authenticate": "Bearer"},
        )

    auth = await authenticate_access_token(credentials.credentials)
    if auth is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token 无效或已过期",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return auth


async def optional_auth(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> AuthContext | None:
    """
    Dependency that optionally validates authentication.

    Returns:
        AuthContext if authenticated, None otherwise.
    """
    if not credentials:
        return None

    return await authenticate_access_token(credentials.credentials)
