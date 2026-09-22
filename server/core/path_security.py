"""Shared validation for paths and remote image URLs used by file operations."""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from pydantic import DirectoryPath, FilePath, TypeAdapter, ValidationError


class PathSecurityError(ValueError):
    """Raised when an API-provided path escapes configured media roots."""


DEFAULT_MEDIA_ROOTS = ("/media", "/output", "/incoming", "/library")
DEFAULT_IMAGE_HOSTS = ("image.tmdb.org",)
_DIRECTORY_PATH_ADAPTER = TypeAdapter(DirectoryPath)
_FILE_PATH_ADAPTER = TypeAdapter(FilePath)


def _configured_media_root_values() -> tuple[str, ...]:
    """Return configured root strings without touching user-selected paths."""
    configured = os.getenv("MHTI_ALLOWED_MEDIA_ROOTS", "")
    values = tuple(value.strip() for value in configured.split(",") if value.strip())
    return values or DEFAULT_MEDIA_ROOTS


def _is_within_root(path: str, root: str) -> bool:
    """Compare normalized absolute path strings with a component boundary."""
    root_prefix = f"{root.rstrip(os.sep)}{os.sep}"
    return path == root or path.startswith(root_prefix)


def allowed_media_roots() -> tuple[Path, ...]:
    """Return normalized roots that file-changing operations may access."""
    return tuple(
        Path(os.path.realpath(os.path.abspath(os.path.normpath(value))))
        for value in _configured_media_root_values()
    )


def validate_media_path(
    raw_path: str,
    *,
    must_exist: bool = False,
    require_file: bool = False,
) -> Path:
    """Resolve a path and ensure it stays under an explicitly allowed media root."""
    if not raw_path or "\x00" in raw_path:
        raise PathSecurityError("路径不能为空或包含非法字符")

    if not os.path.isabs(raw_path):
        raise PathSecurityError("只允许使用绝对路径")

    # Reject lexical escapes before resolving the user-selected path. This
    # prevents different errors from revealing whether an out-of-scope path
    # exists, while still accepting either spelling of a configured symlink root.
    normalized = os.path.abspath(os.path.normpath(raw_path))
    candidate_roots: list[str] = []
    for configured_root in _configured_media_root_values():
        lexical_root = os.path.abspath(os.path.normpath(configured_root))
        resolved_root = os.path.realpath(lexical_root)
        if _is_within_root(normalized, lexical_root) or _is_within_root(
            normalized,
            resolved_root,
        ):
            candidate_roots.append(resolved_root)
    if not candidate_roots:
        raise PathSecurityError(f"路径不在允许的媒体目录中: {normalized}")

    try:
        # realpath is the CodeQL-recommended normalization when symlinks are in
        # scope. The lexical check above occurs before this filesystem probe.
        resolved_text = os.path.realpath(normalized, strict=must_exist)
    except FileNotFoundError as exc:
        raise PathSecurityError(f"Path not found: {raw_path}") from exc
    except (OSError, RuntimeError) as exc:
        raise PathSecurityError(f"路径无效: {raw_path}") from exc

    if not any(_is_within_root(resolved_text, root) for root in candidate_roots):
        raise PathSecurityError(f"路径不在允许的媒体目录中: {resolved_text}")

    resolved = Path(resolved_text)
    if require_file:
        try:
            resolved = Path(_FILE_PATH_ADAPTER.validate_python(resolved))
        except ValidationError as exc:
            raise PathSecurityError(f"路径不是文件: {resolved}") from exc
    return resolved


def validate_media_directory(raw_path: str) -> Path:
    """Return an existing directory confined to an allowed media root."""
    resolved = validate_media_path(raw_path, must_exist=True)
    try:
        # Pydantic performs the type check after our root and symlink boundary
        # validation; callers only receive the canonical validated path.
        return Path(_DIRECTORY_PATH_ADAPTER.validate_python(resolved))
    except ValidationError as exc:
        raise PathSecurityError(f"路径不是目录: {resolved}") from exc


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
