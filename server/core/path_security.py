"""Shared validation for paths and remote image URLs used by file operations."""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


class PathSecurityError(ValueError):
    """Raised when an API-provided path escapes configured media roots."""


DEFAULT_MEDIA_ROOTS = ("/media", "/output", "/incoming", "/library")
DEFAULT_IMAGE_HOSTS = ("image.tmdb.org",)


def allowed_media_roots() -> tuple[Path, ...]:
    """Return normalized roots that file-changing operations may access."""
    configured = os.getenv("MHTI_ALLOWED_MEDIA_ROOTS", "")
    values = [value.strip() for value in configured.split(",") if value.strip()]
    if not values:
        values = list(DEFAULT_MEDIA_ROOTS)
    return tuple(Path(value).resolve(strict=False) for value in values)


def validate_media_path(
    raw_path: str,
    *,
    must_exist: bool = False,
    require_file: bool = False,
    require_directory: bool = False,
) -> Path:
    """Resolve a path and ensure it stays under an explicitly allowed media root."""
    if not raw_path or "\x00" in raw_path:
        raise PathSecurityError("路径不能为空或包含非法字符")

    path = Path(raw_path)
    if not path.is_absolute():
        raise PathSecurityError("只允许使用绝对路径")

    if require_file and require_directory:
        raise ValueError("require_file 与 require_directory 不能同时启用")

    try:
        # The resolved value is checked against configured roots before it is
        # returned to any caller that performs file I/O.
        # codeql[py/path-injection]
        resolved = path.resolve(strict=must_exist)
    except FileNotFoundError as exc:
        raise PathSecurityError(f"Path not found: {raw_path}") from exc
    except (OSError, RuntimeError) as exc:
        raise PathSecurityError(f"路径无效: {raw_path}") from exc

    if not any(
        resolved == root or resolved.is_relative_to(root)
        for root in allowed_media_roots()
    ):
        raise PathSecurityError(f"路径不在允许的媒体目录中: {resolved}")

    # These probes only run after the resolved path is confined to an allowed root.
    # codeql[py/path-injection]
    if must_exist and not resolved.exists():
        raise PathSecurityError(f"路径不存在: {resolved}")
    # codeql[py/path-injection]
    if require_file and not resolved.is_file():
        raise PathSecurityError(f"路径不是文件: {resolved}")
    # codeql[py/path-injection]
    if require_directory and not resolved.is_dir():
        raise PathSecurityError(f"路径不是目录: {resolved}")
    return resolved


def validate_image_url(url: str) -> str:
    """Allow HTTPS image downloads only from configured remote hosts."""
    if any(character in url for character in ("\r", "\n", "\x00")):
        raise PathSecurityError("图片地址包含非法字符")
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise PathSecurityError("图片地址必须使用 HTTPS")
    if parsed.username or parsed.password:
        raise PathSecurityError("图片地址不能包含认证信息")
    try:
        port = parsed.port
    except ValueError as exc:
        raise PathSecurityError("图片地址端口无效") from exc
    if port not in (None, 443):
        raise PathSecurityError("图片地址只能使用 HTTPS 默认端口")
    if parsed.fragment:
        raise PathSecurityError("图片地址不能包含片段")

    configured = os.getenv("MHTI_ALLOWED_IMAGE_HOSTS", "")
    hosts = {
        value.strip().lower().rstrip(".")
        for value in configured.split(",")
        if value.strip()
    }
    if not hosts:
        hosts = set(DEFAULT_IMAGE_HOSTS)

    hostname = parsed.hostname.lower().rstrip(".")
    if hostname not in hosts:
        raise PathSecurityError(f"不允许从该主机下载图片: {hostname}")
    # Rebuild the authority from the validated hostname so URL parsers cannot
    # disagree about user-info, casing, trailing dots, or an explicit port.
    return urlunsplit(("https", hostname, parsed.path or "/", parsed.query, ""))
