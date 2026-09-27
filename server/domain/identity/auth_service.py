"""Authentication service - database-backed."""

import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from jwt import InvalidTokenError as JWTError

from server.infrastructure.db import get_db_manager
from server.infrastructure.repositories.auth_repository import AuthRepository
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
        return await self._repo.count_admins() > 0

    async def register_admin(self, username: str, password: str) -> bool:
        """Register admin account. Returns True if successful."""
        if await self.is_initialized():
            return False

        hash_value, salt = self._hash_password(password)
        password_hash = f"{salt}${hash_value}"

        try:
            await self._repo.insert_admin(username, password_hash)
        except Exception as e:
            logger.error(f"Failed to create admin account: {e}")
            return False

        logger.info(f"Admin account created: {username}")
        return True

    async def verify_credentials(self, username: str, password: str) -> bool:
        """Verify username and password from database."""
        stored_hash = await self._repo.get_password_hash(username)
        if not stored_hash:
            return False
        return self._verify_password(password, stored_hash)

    async def get_user_id(self, username: str) -> int | None:
        """Get user ID by username."""
        return await self._repo.get_user_id(username)

    async def get_username_by_id(self, user_id: int) -> str | None:
        """Get username by user ID."""
        return await self._repo.get_username_by_id(user_id)

    async def is_locked(self, client_ip: str) -> tuple[bool, int]:
        """Check if client is locked out. Returns (is_locked, remaining_minutes)."""
        config = await self._get_config()

        row = await self._repo.get_login_attempts(client_ip)
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
            await self._repo.delete_login_attempts(client_ip)
            return False, 0

        remaining = int((lockout_end - now).total_seconds() / 60) + 1
        return True, remaining

    async def record_failed_attempt(self, client_ip: str) -> int:
        """Record a failed login attempt. Returns remaining attempts."""
        config = await self._get_config()
        now = datetime.now(timezone.utc).isoformat()

        attempts = await self._repo.record_failed_attempt(client_ip, now)
        return max(0, config.max_login_attempts - attempts)

    async def clear_failed_attempts(self, client_ip: str) -> None:
        """Clear failed attempts for a client."""
        await self._repo.delete_login_attempts(client_ip)

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
        except JWTError as e:
            logger.debug(f"Token verification failed: {e}")
            return None, None

    def get_refresh_expire_seconds(self, expire_option: ExpireOption) -> int:
        """Get refresh token expiration in seconds."""
        hours = EXPIRE_HOURS_MAP.get(expire_option, 24 * 7)
        return hours * 3600

    async def change_password(self, username: str, old_password: str, new_password: str) -> bool:
        """Change user password."""
        if not await self.verify_credentials(username, old_password):
            return False

        hash_value, salt = self._hash_password(new_password)
        password_hash = f"{salt}${hash_value}"

        await self._repo.update_password(username, password_hash)
        logger.info(f"Password changed for user: {username}")
        return True

    async def update_username(self, current_username: str, new_username: str, password: str) -> tuple[bool, str]:
        """Update username. Returns (success, message)."""
        # 验证密码
        if not await self.verify_credentials(current_username, password):
            return False, "密码验证失败"

        # 检查新用户名是否已存在
        if await self._repo.username_exists_other(new_username, current_username):
            return False, "用户名已存在"

        # 更新用户名
        await self._repo.update_username(new_username, current_username)
        logger.info(f"Username changed from {current_username} to {new_username}")
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
        logger.info(f"Avatar updated for user: {username}")
        return True, "头像更新成功"

    async def delete_avatar(self, username: str) -> bool:
        """Delete user avatar."""
        await self._repo.delete_avatar(username)
        logger.info(f"Avatar deleted for user: {username}")
        return True


# Singleton instance
auth_service = AuthService()
