"""Session management service - database-backed."""

import hashlib
import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from server.core.db import db_context
from server.models.auth import SessionInfo, LoginHistoryItem, EXPIRE_HOURS_MAP, ExpireOption

logger = logging.getLogger(__name__)


def _parse_user_agent(user_agent: str | None) -> str:
    """Parse user agent to determine device type."""
    if not user_agent:
        return "desktop"
    ua_lower = user_agent.lower()
    if "mobile" in ua_lower or "android" in ua_lower or "iphone" in ua_lower:
        return "mobile"
    if "tablet" in ua_lower or "ipad" in ua_lower:
        return "tablet"
    return "desktop"


def _generate_device_name(user_agent: str | None, ip_address: str | None) -> str:
    """Generate a device name from user agent."""
    if not user_agent:
        return f"Unknown Device ({ip_address or 'unknown'})"
    ua_lower = user_agent.lower()
    if "windows" in ua_lower:
        os_name = "Windows"
    elif "mac" in ua_lower:
        os_name = "macOS"
    elif "linux" in ua_lower:
        os_name = "Linux"
    elif "android" in ua_lower:
        os_name = "Android"
    elif "iphone" in ua_lower or "ipad" in ua_lower:
        os_name = "iOS"
    else:
        os_name = "Unknown OS"

    if "chrome" in ua_lower:
        browser = "Chrome"
    elif "firefox" in ua_lower:
        browser = "Firefox"
    elif "safari" in ua_lower:
        browser = "Safari"
    elif "edge" in ua_lower:
        browser = "Edge"
    else:
        browser = "Browser"

    return f"{os_name} - {browser}"


class SessionService:
    """Service for managing user sessions with database connection pool."""

    async def is_session_active(self, session_id: str, username: str) -> bool:
        """Return whether a non-expired session still belongs to the JWT subject."""
        now = datetime.now(timezone.utc).isoformat()
        async with db_context() as db:
            cursor = await db.execute(
                """
                SELECT 1
                FROM sessions AS s
                JOIN admin AS a ON a.id = s.user_id
                WHERE s.id = ? AND a.username = ? AND s.expires_at > ?
                LIMIT 1
                """,
                (session_id, username, now),
            )
            return await cursor.fetchone() is not None

    async def _get_max_sessions(self) -> int:
        """Get max sessions from config."""
        from server.services.auth_config_service import get_auth_config_service_async
        service = await get_auth_config_service_async()
        config = await service.get_auth_config()
        return config.max_sessions

    async def create_session(
        self,
        user_id: int,
        expire_option: ExpireOption,
        ip_address: str | None = None,
        user_agent: str | None = None,
        device_name: str | None = None,
    ) -> tuple[str, str]:
        """
        Create a new session.

        Returns:
            Tuple of (session_id, refresh_token)
        """
        session_id = str(uuid.uuid4())
        refresh_token = secrets.token_urlsafe(32)
        refresh_token_hash = hashlib.sha256(refresh_token.encode()).hexdigest()

        expire_hours = EXPIRE_HOURS_MAP.get(expire_option, 24 * 7)
        expires_at = datetime.now(timezone.utc) + timedelta(hours=expire_hours)

        device_type = _parse_user_agent(user_agent)
        if not device_name:
            device_name = _generate_device_name(user_agent, ip_address)

        # Cleanup excess sessions
        await self._cleanup_excess_sessions(user_id)

        async with db_context() as db:
            await db.execute(
                """
                INSERT INTO sessions
                (id, user_id, refresh_token_hash, device_name, device_type,
                 ip_address, user_agent, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    user_id,
                    refresh_token_hash,
                    device_name,
                    device_type,
                    ip_address,
                    user_agent,
                    expires_at.isoformat(),
                ),
            )
            await db.commit()

        logger.info(f"Session created: {session_id[:8]}... for user {user_id}")
        return session_id, refresh_token

    async def _cleanup_excess_sessions(self, user_id: int) -> None:
        """Remove oldest sessions if exceeding max limit."""
        max_sessions = await self._get_max_sessions()

        async with db_context() as db:
            cursor = await db.execute(
                "SELECT COUNT(*) FROM sessions WHERE user_id = ?", (user_id,)
            )
            row = await cursor.fetchone()
            count = row[0] if row else 0

            if count >= max_sessions:
                # Delete oldest sessions
                await db.execute(
                    """
                    DELETE FROM sessions WHERE id IN (
                        SELECT id FROM sessions
                        WHERE user_id = ?
                        ORDER BY last_used_at ASC
                        LIMIT ?
                    )
                    """,
                    (user_id, count - max_sessions + 1),
                )
                await db.commit()
                logger.info(f"Cleaned up {count - max_sessions + 1} old sessions for user {user_id}")

    async def verify_refresh_token(self, refresh_token: str) -> tuple[str | None, int | None]:
        """
        Verify refresh token and return session_id and user_id if valid.

        Returns:
            Tuple of (session_id, user_id) or (None, None) if invalid
        """
        token_hash = hashlib.sha256(refresh_token.encode()).hexdigest()
        now = datetime.now(timezone.utc).isoformat()

        async with db_context() as db:
            # Debug query
            cursor = await db.execute(
                "SELECT id, user_id, expires_at FROM sessions WHERE refresh_token_hash = ?",
                (token_hash,),
            )
            debug_row = await cursor.fetchone()
            if debug_row:
                logger.debug(f"Found session: id={debug_row[0]}, expires_at={debug_row[2]}")
                if debug_row[2] <= now:
                    logger.warning(f"Session expired: expires_at={debug_row[2]} <= now={now}")
            else:
                logger.debug("No matching session found (token hash mismatch)")

            # Actual verification
            cursor = await db.execute(
                """
                SELECT id, user_id FROM sessions
                WHERE refresh_token_hash = ? AND expires_at > ?
                """,
                (token_hash, now),
            )
            row = await cursor.fetchone()

            if not row:
                return None, None

            # Update last used time
            await db.execute(
                "UPDATE sessions SET last_used_at = ? WHERE id = ?",
                (now, row[0]),
            )
            await db.commit()

            logger.debug(f"Refresh token verified: session_id={row[0]}, user_id={row[1]}")
            return row[0], row[1]

    async def revoke_session(self, session_id: str) -> bool:
        """Revoke a session by ID."""
        async with db_context() as db:
            cursor = await db.execute(
                "DELETE FROM sessions WHERE id = ?", (session_id,)
            )
            await db.commit()
            deleted = cursor.rowcount > 0
            if deleted:
                logger.info(f"Session revoked: {session_id[:8]}...")
            return deleted

    async def revoke_all_sessions(self, user_id: int, except_session_id: str | None = None) -> int:
        """Revoke all sessions for a user, optionally except one."""
        async with db_context() as db:
            if except_session_id:
                cursor = await db.execute(
                    "DELETE FROM sessions WHERE user_id = ? AND id != ?",
                    (user_id, except_session_id),
                )
            else:
                cursor = await db.execute(
                    "DELETE FROM sessions WHERE user_id = ?", (user_id,)
                )
            await db.commit()
            count = cursor.rowcount
            logger.info(f"Revoked {count} sessions for user {user_id}")
            return count

    async def get_sessions(self, user_id: int, current_session_id: str | None = None) -> list[SessionInfo]:
        """Get all active sessions for a user."""
        now = datetime.now(timezone.utc).isoformat()

        async with db_context() as db:
            # Clean up expired sessions
            await db.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
            await db.commit()

            cursor = await db.execute(
                """
                SELECT id, device_name, device_type, ip_address,
                       created_at, last_used_at, expires_at
                FROM sessions
                WHERE user_id = ?
                ORDER BY last_used_at DESC
                """,
                (user_id,),
            )
            rows = await cursor.fetchall()

        sessions = []
        for row in rows:
            sessions.append(
                SessionInfo(
                    id=row[0],
                    device_name=row[1] or "Unknown",
                    device_type=row[2] or "desktop",
                    ip_address=row[3] or "unknown",
                    created_at=datetime.fromisoformat(row[4]),
                    last_used_at=datetime.fromisoformat(row[5]),
                    is_current=row[0] == current_session_id,
                    expires_at=datetime.fromisoformat(row[6]),
                )
            )
        return sessions

    async def record_login(
        self,
        username: str,
        success: bool,
        ip_address: str | None = None,
        user_agent: str | None = None,
        device_name: str | None = None,
        failure_reason: str | None = None,
        session_id: str | None = None,
    ) -> None:
        """Record a login attempt in history."""
        if not device_name and user_agent:
            device_name = _generate_device_name(user_agent, ip_address)

        async with db_context() as db:
            await db.execute(
                """
                INSERT INTO login_history
                (username, ip_address, user_agent, device_name, success, failure_reason, session_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (username, ip_address, user_agent, device_name, 1 if success else 0, failure_reason, session_id),
            )
            await db.commit()

        log_msg = f"Login {'success' if success else 'failed'}: {username} from {ip_address}"
        if failure_reason:
            log_msg += f" ({failure_reason})"
        logger.info(log_msg)

    async def get_login_history(
        self, username: str, limit: int = 20, offset: int = 0
    ) -> tuple[list[LoginHistoryItem], int]:
        """Get login history for a user."""
        async with db_context() as db:
            # Get total count
            cursor = await db.execute(
                "SELECT COUNT(*) FROM login_history WHERE username = ?", (username,)
            )
            row = await cursor.fetchone()
            total = row[0] if row else 0

            # Get records
            cursor = await db.execute(
                """
                SELECT id, ip_address, user_agent, device_name, login_time, success, failure_reason
                FROM login_history
                WHERE username = ?
                ORDER BY login_time DESC
                LIMIT ? OFFSET ?
                """,
                (username, limit, offset),
            )
            rows = await cursor.fetchall()

        items = []
        for row in rows:
            items.append(
                LoginHistoryItem(
                    id=row[0],
                    ip_address=row[1] or "unknown",
                    device_name=row[2],
                    user_agent=row[3],
                    login_time=datetime.fromisoformat(row[4]),
                    success=bool(row[5]),
                    failure_reason=row[6],
                )
            )
        return items, total

    async def cleanup_old_history(self, days: int = 90) -> int:
        """Clean up login history older than specified days."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

        async with db_context() as db:
            cursor = await db.execute(
                "DELETE FROM login_history WHERE login_time < ?",
                (cutoff.isoformat(),)
            )
            await db.commit()
            count = cursor.rowcount
            if count > 0:
                logger.info(f"Cleaned up {count} old login history records")
            return count


# Singleton instance
session_service = SessionService()
