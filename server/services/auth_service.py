"""Authentication service - database-backed."""

import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
import aiosqlite
from jwt.exceptions import InvalidTokenError

from server.core.database import get_db_manager
from server.models.auth import ExpireOption, EXPIRE_HOURS_MAP, AuthConfig

logger = logging.getLogger(__name__)


class AuthService:
    """Service for authentication with database-backed configuration."""

    ALGORITHM = "HS256"
    _config_cache: AuthConfig | None = None

    async def _get_config(self) -> AuthConfig:
        """Get authentication configuration from database."""
        if self._config_cache:
            return self._config_cache

        from server.services.auth_config_service import get_auth_config_service_async
        service = await get_auth_config_service_async()
        self._config_cache = await service.get_auth_config()
        return self._config_cache

    def _get_config_sync(self) -> AuthConfig:
        """Get cached config synchronously (for token operations)."""
        if self._config_cache:
            return self._config_cache
        # Fallback - should not happen after proper initialization
        from server.core.config import get_app_config
        return get_app_config().auth

    def refresh_config_cache(self) -> None:
        """Clear config cache to force reload."""
        self._config_cache = None

    def _hash_password(self, password: str, salt: str | None = None) -> tuple[str, str]:
        """Hash password with salt. Returns (hash, salt)."""
        if salt is None:
            salt = secrets.token_hex(16)
        hashed = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100000)
        return hashed.hex(), salt

    def _verify_password(self, password: str, stored_hash: str) -> bool:
        """Verify password against stored hash (format: salt$hash)."""
        if "$" not in stored_hash:
            return False
        salt, hash_value = stored_hash.split("$", 1)
        computed_hash, _ = self._hash_password(password, salt)
        return secrets.compare_digest(computed_hash, hash_value)

    async def is_initialized(self) -> bool:
        """Check if admin account exists."""
        manager = await get_db_manager()
        async with manager.get_connection() as db:
            cursor = await db.execute("SELECT COUNT(*) FROM admin")
            row = await cursor.fetchone()
            return row[0] > 0 if row else False

    async def register_admin(self, username: str, password: str) -> bool:
        """Register admin account. Returns True if successful."""
        hash_value, salt = self._hash_password(password)
        password_hash = f"{salt}${hash_value}"

        manager = await get_db_manager()
        async with manager.get_connection() as db:
            try:
                # Serialize the singleton check and insert. The database trigger
                # remains the final invariant if another code path inserts.
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
                logger.info(f"Admin account created: {username}")
                return True
            except aiosqlite.IntegrityError:
                await db.rollback()
                logger.warning("Concurrent administrator registration was rejected")
                return False
            except Exception as e:
                await db.rollback()
                logger.error(f"Failed to create admin account: {e}")
                return False

    async def verify_credentials(self, username: str, password: str) -> bool:
        """Verify username and password from database."""
        manager = await get_db_manager()
        async with manager.get_connection() as db:
            cursor = await db.execute(
                "SELECT password_hash FROM admin WHERE username = ?", (username,)
            )
            row = await cursor.fetchone()
            if not row:
                return False
            return self._verify_password(password, row[0])

    async def get_user_id(self, username: str) -> int | None:
        """Get user ID by username."""
        manager = await get_db_manager()
        async with manager.get_connection() as db:
            cursor = await db.execute(
                "SELECT id FROM admin WHERE username = ?", (username,)
            )
            row = await cursor.fetchone()
            return row[0] if row else None

    async def get_username_by_id(self, user_id: int) -> str | None:
        """Get username by user ID."""
        manager = await get_db_manager()
        async with manager.get_connection() as db:
            cursor = await db.execute(
                "SELECT username FROM admin WHERE id = ?", (user_id,)
            )
            row = await cursor.fetchone()
            return row[0] if row else None

    async def is_locked(self, client_ip: str) -> tuple[bool, int]:
        """Check if client is locked out. Returns (is_locked, remaining_minutes)."""
        config = await self._get_config()

        manager = await get_db_manager()
        async with manager.get_connection() as db:
            cursor = await db.execute(
                "SELECT attempts, last_attempt FROM login_attempts WHERE client_ip = ?",
                (client_ip,)
            )
            row = await cursor.fetchone()

            if not row:
                return False, 0

            attempts = row[0]
            last_attempt_str = row[1]

            if attempts < config.max_login_attempts:
                return False, 0

            try:
                last_attempt = datetime.fromisoformat(last_attempt_str)
                if last_attempt.tzinfo is None:
                    last_attempt = last_attempt.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                return False, 0

            lockout_end = last_attempt + timedelta(minutes=config.lockout_minutes)
            now = datetime.now(timezone.utc)

            if now >= lockout_end:
                await db.execute(
                    "DELETE FROM login_attempts WHERE client_ip = ?", (client_ip,)
                )
                await db.commit()
                return False, 0

            remaining = int((lockout_end - now).total_seconds() / 60) + 1
            return True, remaining

    async def record_failed_attempt(self, client_ip: str) -> int:
        """Record a failed login attempt. Returns remaining attempts."""
        config = await self._get_config()
        now = datetime.now(timezone.utc).isoformat()

        manager = await get_db_manager()
        async with manager.get_connection() as db:
            await db.execute("""
                INSERT INTO login_attempts (client_ip, attempts, last_attempt)
                VALUES (?, 1, ?)
                ON CONFLICT(client_ip) DO UPDATE SET
                    attempts = attempts + 1,
                    last_attempt = excluded.last_attempt
            """, (client_ip, now))
            await db.commit()

            cursor = await db.execute(
                "SELECT attempts FROM login_attempts WHERE client_ip = ?",
                (client_ip,)
            )
            row = await cursor.fetchone()
            attempts = row[0] if row else 1

        return max(0, config.max_login_attempts - attempts)

    async def clear_failed_attempts(self, client_ip: str) -> None:
        """Clear failed attempts for a client."""
        manager = await get_db_manager()
        async with manager.get_connection() as db:
            await db.execute(
                "DELETE FROM login_attempts WHERE client_ip = ?", (client_ip,)
            )
            await db.commit()

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
            username = payload.get("sub")
            session_id = payload.get("sid")
            return username, session_id
        except InvalidTokenError as e:
            logger.debug(f"Token verification failed: {e}")
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
        """Change a password and revoke other sessions in one transaction."""
        hash_value, salt = self._hash_password(new_password)
        password_hash = f"{salt}${hash_value}"

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
                    "DELETE FROM sessions WHERE user_id = ? AND id != ?",
                    (user_id, except_session_id),
                )
                await db.commit()
            except BaseException:
                await db.rollback()
                raise

        logger.info("Password changed for user: %s", username)
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
                    "UPDATE login_history SET username = ? WHERE username = ?",
                    (new_username, current_username),
                )
                await db.commit()
            except aiosqlite.IntegrityError:
                await db.rollback()
                return False, "用户名已存在"
            except BaseException:
                await db.rollback()
                raise
        logger.info("Username changed from %s to %s", current_username, new_username)
        return True, "用户名修改成功"

    async def get_user_profile(self, username: str) -> dict | None:
        """Get user profile including avatar."""
        manager = await get_db_manager()
        async with manager.get_connection() as db:
            cursor = await db.execute(
                "SELECT id, username, avatar, created_at FROM admin WHERE username = ?",
                (username,)
            )
            row = await cursor.fetchone()
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

        manager = await get_db_manager()
        async with manager.get_connection() as db:
            await db.execute(
                "UPDATE admin SET avatar = ? WHERE username = ?",
                (avatar_data, username)
            )
            await db.commit()
            logger.info(f"Avatar updated for user: {username}")
            return True, "头像更新成功"

    async def delete_avatar(self, username: str) -> bool:
        """Delete user avatar."""
        manager = await get_db_manager()
        async with manager.get_connection() as db:
            await db.execute(
                "UPDATE admin SET avatar = NULL WHERE username = ?",
                (username,)
            )
            await db.commit()
            logger.info(f"Avatar deleted for user: {username}")
            return True


# Singleton instance
auth_service = AuthService()
