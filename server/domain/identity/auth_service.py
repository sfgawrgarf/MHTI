"""Authentication service - database-backed."""

import asyncio
import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import aiosqlite
import jwt
from jwt import InvalidTokenError as JWTError

from server.infrastructure.db import get_db_manager
from server.infrastructure.repositories.auth_repository import AuthRepository
from server.infrastructure.log_security import safe_log_value
from server.models.auth import ExpireOption, EXPIRE_HOURS_MAP, AuthConfig

logger = logging.getLogger(__name__)


async def _manager_getter():
    """运行时读取模块全局 get_db_manager，保留测试对该符号的替换接缝。"""
    return await get_db_manager()


class AuthService:
    """Service for authentication with database-backed configuration."""

    ALGORITHM = "HS256"
    _config_cache: AuthConfig | None = None

    def __init__(self) -> None:
        self._repo = AuthRepository(manager_getter=_manager_getter)

    async def _get_config(self) -> AuthConfig:
        """Get authentication configuration from database."""
        if self._config_cache:
            return self._config_cache

        from server.domain.identity.auth_config_service import get_auth_config_service_async
        service = await get_auth_config_service_async()
        self._config_cache = await service.get_auth_config()
        return self._config_cache

    def _get_config_sync(self) -> AuthConfig:
        """Get cached config synchronously (for token operations)."""
        if self._config_cache:
            return self._config_cache
        # Fallback - should not happen after proper initialization
        from server.infrastructure.config import get_app_config
        return get_app_config().auth

    def refresh_config_cache(self) -> None:
        """Clear config cache to force reload."""
        self._config_cache = None

    # Adapted from upstream v2.0.1; see licenses/MHTI-upstream/.
    _PBKDF2_ALGORITHM = "pbkdf2_sha256"
    _PBKDF2_ITERATIONS = 600_000
    _LEGACY_ITERATIONS = 100_000

    def _hash_password(
        self, password: str, salt: str | None = None, iterations: int | None = None,
    ) -> tuple[str, str]:
        """Hash password with salt. Returns (hash, salt)."""
        if salt is None:
            salt = secrets.token_hex(16)
        rounds = self._PBKDF2_ITERATIONS if iterations is None else iterations
        hashed = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), rounds)
        return hashed.hex(), salt

    def _format_password_hash(self, password: str) -> str:
        hashed, salt = self._hash_password(password)
        return f"{self._PBKDF2_ALGORITHM}${self._PBKDF2_ITERATIONS}${salt}${hashed}"

    @classmethod
    def _parse_password_hash(cls, stored_hash: str) -> tuple[int, str, str] | None:
        parts = stored_hash.split("$")
        if len(parts) == 2:
            rounds, salt, expected = cls._LEGACY_ITERATIONS, *parts
        elif len(parts) == 4 and parts[0] == cls._PBKDF2_ALGORITHM:
            try:
                rounds, salt, expected = int(parts[1]), parts[2], parts[3]
            except ValueError:
                return None
        else:
            return None
        if not 1 <= rounds <= 5_000_000 or not salt or len(expected) != 64:
            return None
        try:
            bytes.fromhex(expected)
        except ValueError:
            return None
        return rounds, salt, expected

    def _verify_password(self, password: str, stored_hash: str) -> bool:
        """Verify both versioned PBKDF2 hashes and legacy salt$hash values."""
        parsed = self._parse_password_hash(stored_hash)
        if parsed is None:
            return False
        rounds, salt, expected = parsed
        computed_hash, _ = self._hash_password(password, salt, rounds)
        return secrets.compare_digest(computed_hash, expected)

    async def is_initialized(self) -> bool:
        """Check if admin account exists."""
        manager = await get_db_manager()
        async with manager.get_connection() as db:
            cursor = await db.execute("SELECT COUNT(*) FROM admin")
            row = await cursor.fetchone()
            return bool(row and row[0] > 0)

    async def register_admin(self, username: str, password: str) -> bool:
        """Register admin account. Returns True if successful."""
        password_hash = await asyncio.to_thread(self._format_password_hash, password)

        manager = await get_db_manager()
        async with manager.get_connection() as db:
            try:
                # Serialize the singleton check and insert. The database trigger
                # remains the final invariant for any other insertion path.
                await db.execute("BEGIN IMMEDIATE")
                cursor = await db.execute("SELECT 1 FROM admin LIMIT 1")
                if await cursor.fetchone():
                    await db.rollback()
                    return False
                await db.execute(
                    "INSERT INTO admin (username, password_hash) VALUES (?, ?)",
                    (username, password_hash),
                )
                await db.commit()
            except aiosqlite.IntegrityError:
                await db.rollback()
                logger.warning("Concurrent administrator registration was rejected")
                return False
            except Exception as exc:
                await db.rollback()
                logger.error("Failed to create admin account: %s", safe_log_value(exc))
                return False

        logger.info("Admin account created: %s", safe_log_value(username))
        return True

    async def verify_credentials(self, username: str, password: str) -> bool:
        """Verify username and password from database."""
        return await self.verify_credential_stamp(username, password) is not None

    async def verify_credential_stamp(self, username: str, password: str) -> str | None:
        """Return the exact verified credential, for transactional session creation."""
        manager = await get_db_manager()
        async with manager.get_connection() as db:
            cursor = await db.execute(
                "SELECT password_hash FROM admin WHERE username = ?", (username,)
            )
            row = await cursor.fetchone()
        if row is None or not await asyncio.to_thread(self._verify_password, password, row[0]):
            return None
        parsed = self._parse_password_hash(row[0])
        if parsed is not None and (parsed[0] < self._PBKDF2_ITERATIONS or row[0].count("$") == 1):
            upgraded = await asyncio.to_thread(self._format_password_hash, password)
            async with manager.get_connection() as db:
                cursor = await db.execute(
                    "UPDATE admin SET password_hash = ? WHERE username = ? AND password_hash = ?",
                    (upgraded, username, row[0]),
                )
                await db.commit()
                # A simultaneous password change must not be undone or accepted.
                if cursor.rowcount != 1:
                    cursor = await db.execute(
                        "SELECT password_hash FROM admin WHERE username = ?", (username,),
                    )
                    current = await cursor.fetchone()
                    if current and await asyncio.to_thread(self._verify_password, password, current[0]):
                        return str(current[0])
                    return None
            return upgraded
        return str(row[0])

    async def get_user_id(self, username: str) -> int | None:
        """Get user ID by username."""
        manager = await get_db_manager()
        async with manager.get_connection() as db:
            cursor = await db.execute(
                "SELECT id FROM admin WHERE username = ?", (username,)
            )
            row = await cursor.fetchone()
            return int(row[0]) if row else None

    async def get_username_by_id(self, user_id: int) -> str | None:
        """Get username by user ID."""
        manager = await get_db_manager()
        async with manager.get_connection() as db:
            cursor = await db.execute(
                "SELECT username FROM admin WHERE id = ?", (user_id,)
            )
            row = await cursor.fetchone()
            return str(row[0]) if row else None

    async def is_locked(self, username: str, client_ip: str) -> tuple[bool, int]:
        """Check both account and IP lockouts.

        Keeping both dimensions prevents an attacker from bypassing the limit
        by changing only the source IP or only the submitted username.
        """
        config = await self._get_config()

        now = datetime.now(timezone.utc)
        remaining_minutes: list[int] = []
        for row in await self._repo.get_login_attempts(client_ip, username):
            scope = str(row[0])
            attempts = int(row[1] or 0)
            last_attempt_str = row[2]
            if attempts < config.max_login_attempts:
                continue

            try:
                last_attempt = datetime.fromisoformat(last_attempt_str)
                if last_attempt.tzinfo is None:
                    last_attempt = last_attempt.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                key = username if scope == "account" else client_ip
                await self._repo.delete_login_attempt_scope(scope, key)
                continue

            lockout_end = last_attempt + timedelta(minutes=config.lockout_minutes)
            if now >= lockout_end:
                key = username if scope == "account" else client_ip
                await self._repo.delete_login_attempt_scope(scope, key)
                continue

            remaining_minutes.append(
                int((lockout_end - now).total_seconds() / 60) + 1
            )

        if not remaining_minutes:
            return False, 0
        return True, min(remaining_minutes)

    async def record_failed_attempt(self, username: str, client_ip: str) -> int:
        """Record a failed login in both dimensions and return remaining tries."""
        config = await self._get_config()
        now = datetime.now(timezone.utc).isoformat()

        ip_attempts, account_attempts = await self._repo.record_failed_attempt(
            client_ip, username, now
        )
        attempts = max(ip_attempts, account_attempts)
        return max(0, config.max_login_attempts - attempts)

    async def clear_failed_attempts(self, username: str, client_ip: str) -> None:
        """Clear both failed-login counters after a successful login."""
        await self._repo.delete_login_attempts(client_ip, username)

    def create_access_token(self, username: str, session_id: str) -> tuple[str, int]:
        """
        Create short-lived access token.

        Returns:
            Tuple of (token, expires_in_seconds)
        """
        config = self._get_config_sync()
        expires_in = config.access_token_minutes * 60
        expire = datetime.now(timezone.utc) + timedelta(minutes=config.access_token_minutes)

        payload: dict[str, Any] = {
            "sub": username,
            "sid": session_id,
            "exp": expire,
            "type": "access",
        }
        token = jwt.encode(payload, config.jwt_secret, algorithm=self.ALGORITHM)
        return token, expires_in

    def verify_token(self, token: str) -> tuple[str | None, str | None]:
        """
        Verify JWT token.

        Returns:
            Tuple of (username, session_id) if valid, (None, None) otherwise
        """
        config = self._get_config_sync()
        try:
            payload = jwt.decode(token, config.jwt_secret, algorithms=[self.ALGORITHM])
            if payload.get("type") != "access":
                return None, None
            username = payload.get("sub")
            session_id = payload.get("sid")
            if not isinstance(username, str) or not username:
                return None, None
            if not isinstance(session_id, str) or not session_id:
                return None, None
            return username, session_id
        except JWTError as e:
            logger.debug("Token verification failed: %s", safe_log_value(e))
            return None, None

    def get_refresh_expire_seconds(self, expire_option: ExpireOption) -> int:
        """Get refresh token expiration in seconds."""
        hours = EXPIRE_HOURS_MAP.get(expire_option, 24 * 7)
        return hours * 3600

    async def change_password(
        self,
        username: str,
        old_password: str,
        new_password: str,
        *,
        except_session_id: str,
    ) -> tuple[bool, list[str]]:
        """Change a password and revoke other sessions atomically."""
        password_hash = await asyncio.to_thread(self._format_password_hash, new_password)

        manager = await get_db_manager()
        async with manager.get_connection() as db:
            try:
                await db.execute("BEGIN IMMEDIATE")
                cursor = await db.execute(
                    "SELECT id, password_hash FROM admin WHERE username = ?",
                    (username,),
                )
                row = await cursor.fetchone()
                if row is None or not self._verify_password(old_password, row[1]):
                    await db.rollback()
                    return False, []

                user_id = int(row[0])
                cursor = await db.execute(
                    "SELECT id FROM sessions WHERE user_id = ? AND id != ?",
                    (user_id, except_session_id),
                )
                revoked_ids = [str(item[0]) for item in await cursor.fetchall()]
                await db.execute(
                    "UPDATE admin SET password_hash = ? WHERE id = ?",
                    (password_hash, user_id),
                )
                await db.execute(
                    "DELETE FROM refresh_token_history WHERE session_id IN "
                    "(SELECT id FROM sessions WHERE user_id = ? AND id != ?)",
                    (user_id, except_session_id),
                )
                await db.execute(
                    "DELETE FROM sessions WHERE user_id = ? AND id != ?",
                    (user_id, except_session_id),
                )
                await db.commit()
            except BaseException:
                await db.rollback()
                raise

        logger.info("Password changed for user: %s", safe_log_value(username))
        return True, revoked_ids

    async def update_username(self, current_username: str, new_username: str, password: str) -> tuple[bool, str]:
        """Update username. Returns (success, message)."""
        manager = await get_db_manager()
        async with manager.get_connection() as db:
            try:
                await db.execute("BEGIN IMMEDIATE")
                cursor = await db.execute(
                    "SELECT id, password_hash FROM admin WHERE username = ?",
                    (current_username,),
                )
                row = await cursor.fetchone()
                if row is None or not self._verify_password(password, row[1]):
                    await db.rollback()
                    return False, "密码验证失败"

                cursor = await db.execute(
                    "SELECT 1 FROM admin WHERE username = ? AND id != ?",
                    (new_username, row[0]),
                )
                if await cursor.fetchone():
                    await db.rollback()
                    return False, "用户名已存在"

                await db.execute(
                    "UPDATE admin SET username = ? WHERE id = ?",
                    (new_username, row[0]),
                )
                await db.execute(
                    "UPDATE login_history SET username = ? "
                    "WHERE username = ? AND success = 1",
                    (new_username, current_username),
                )
                await db.commit()
            except BaseException:
                await db.rollback()
                raise

        logger.info(
            "Username changed from %s to %s",
            safe_log_value(current_username),
            safe_log_value(new_username),
        )
        return True, "用户名修改成功"

    async def get_user_profile(self, username: str) -> dict | None:
        """Get user profile including avatar."""
        row = await self._repo.get_profile(username)
        if not row:
            return None
        return {
            "id": row[0],
            "username": row[1],
            "avatar": row[2],
            "created_at": row[3],
        }

    async def update_avatar(self, username: str, avatar_data: str) -> tuple[bool, str]:
        """Update user avatar. avatar_data should be base64 encoded image."""
        # 验证 base64 数据大小（限制 500KB）
        if len(avatar_data) > 500 * 1024:
            return False, "头像文件过大，请选择小于 500KB 的图片"

        await self._repo.update_avatar(username, avatar_data)
        logger.info("Avatar updated for user: %s", safe_log_value(username))
        return True, "头像更新成功"

    async def delete_avatar(self, username: str) -> bool:
        """Delete user avatar."""
        await self._repo.delete_avatar(username)
        logger.info("Avatar deleted for user: %s", safe_log_value(username))
        return True


# Singleton instance
auth_service = AuthService()
