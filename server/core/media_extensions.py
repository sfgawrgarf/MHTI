"""Canonical media-extension handling shared by every scan path."""

from __future__ import annotations


SUPPORTED_VIDEO_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".3gp",
        ".avi",
        ".bdmv",
        ".flv",
        ".iso",
        ".m2ts",
        ".m4v",
        ".mkv",
        ".mov",
        ".mp4",
        ".mpeg",
        ".mpg",
        ".rmvb",
        ".strm",
        ".ts",
        ".vob",
        ".webm",
        ".wmv",
    }
)


def normalize_video_extension(value: str) -> str:
    """Return a lower-case extension with exactly one leading dot."""
    normalized = value.strip().lower()
    if not normalized:
        return ""
    return normalized if normalized.startswith(".") else f".{normalized}"


def normalize_video_extensions(values: list[str]) -> set[str]:
    """Normalize a user-provided extension list and discard blank entries."""
    return {
        normalized
        for value in values
        if (normalized := normalize_video_extension(value))
    }
