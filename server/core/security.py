"""Security utilities for encryption and decryption."""

import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

# 使用与数据库相同的数据目录存储密钥文件
_PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()
_KEY_FILE = _PROJECT_ROOT / "data" / ".secret_key"


def get_encryption_key() -> bytes:
    """Get or generate encryption key."""
    # 确保数据目录存在
    _KEY_FILE.parent.mkdir(parents=True, exist_ok=True)

    if _KEY_FILE.exists():
        _KEY_FILE.chmod(0o600)
        return _KEY_FILE.read_bytes()

    key = Fernet.generate_key()
    try:
        fd = os.open(_KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        # Another process created the key between the existence check and open.
        _KEY_FILE.chmod(0o600)
        return _KEY_FILE.read_bytes()

    with os.fdopen(fd, "wb") as key_file:
        os.fchmod(key_file.fileno(), 0o600)
        key_file.write(key)
    return key


def encrypt(data: str) -> str:
    """Encrypt a string."""
    f = Fernet(get_encryption_key())
    return f.encrypt(data.encode()).decode()


def decrypt(data: str) -> str | None:
    """Decrypt a string. Returns None if decryption fails."""
    try:
        f = Fernet(get_encryption_key())
        return f.decrypt(data.encode()).decode()
    except InvalidToken:
        return None


# Aliases for backward compatibility
encrypt_value = encrypt
decrypt_value = decrypt
