"""Authentication dependencies for API and WebSocket protection."""

import ipaddress
import os

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from server.services.auth_service import auth_service
from server.services.session_service import session_service

security = HTTPBearer(auto_error=False)


class AuthContext:
    """Authentication context with user info."""

    def __init__(self, username: str, session_id: str):
        self.username = username
        self.session_id = session_id


async def authenticate_access_token(token: str) -> AuthContext | None:
    """Validate the JWT and ensure its backing session has not been revoked."""
    username, session_id = auth_service.verify_token(token)
    if not username or not session_id:
        return None
    if not await session_service.is_session_active(session_id, username):
        return None
    return AuthContext(username=username, session_id=session_id)


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


def get_client_ip(request: Request) -> str:
    """Get client IP without trusting attacker-controlled forwarding entries."""
    direct_ip = request.client.host if request.client else "unknown"
    forwarded = request.headers.get("X-Forwarded-For")
    if not forwarded or not _is_trusted_proxy(direct_ip):
        return direct_ip

    addresses = [item.strip() for item in forwarded.split(",") if item.strip()]
    if not addresses:
        return direct_ip

    try:
        trusted_hops = max(1, int(os.getenv("MHTI_TRUSTED_PROXY_HOPS", "1")))
    except ValueError:
        trusted_hops = 1
    index = max(0, len(addresses) - trusted_hops)
    candidate = addresses[index]
    try:
        ipaddress.ip_address(candidate)
    except ValueError:
        return direct_ip
    return candidate


def _is_trusted_proxy(client_ip: str) -> bool:
    """Only honor forwarding headers from configured immediate proxy networks."""
    configured = os.getenv(
        "MHTI_TRUSTED_PROXY_NETWORKS",
        "127.0.0.0/8,::1/128",
    )
    try:
        address = ipaddress.ip_address(client_ip)
    except ValueError:
        return False
    for value in configured.split(","):
        value = value.strip()
        if not value:
            continue
        try:
            if address in ipaddress.ip_network(value, strict=False):
                return True
        except ValueError:
            continue
    return False
