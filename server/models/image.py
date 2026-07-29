"""Image download data models."""

from enum import Enum
from pathlib import Path
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator


class ImageType(str, Enum):
    """Image type enumeration."""

    POSTER = "poster"
    BACKDROP = "backdrop"
    STILL = "still"  # Episode thumbnail
    SEASON_POSTER = "season_poster"


class ImageSize(str, Enum):
    """TMDB image size options."""

    W92 = "w92"
    W154 = "w154"
    W185 = "w185"
    W342 = "w342"
    W500 = "w500"
    W780 = "w780"
    ORIGINAL = "original"


class ImageDownloadRequest(BaseModel):
    """Single image download request."""

    url: str
    save_path: str
    filename: str = Field(min_length=1, max_length=255)

    @field_validator("filename")
    @classmethod
    def validate_filename(cls, value: str) -> str:
        """Reject path traversal and directory components in filenames."""
        if "\x00" in value or value in {".", ".."} or Path(value).name != value:
            raise ValueError("filename 必须是单个文件名")
        if Path(value).suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
            raise ValueError("filename 必须使用受支持的图片扩展名")
        return value

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        """Require a well-formed HTTP(S) URL; the service applies the host policy."""
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("url 必须是有效的 HTTP(S) 地址")
        return value


class ImageDownloadResult(BaseModel):
    """Download result for a single image."""

    url: str
    save_path: str
    success: bool
    error: str | None = None


class BatchDownloadRequest(BaseModel):
    """Batch download request."""

    images: list[ImageDownloadRequest] = Field(default_factory=list, max_length=100)
    concurrency: int = Field(default=3, ge=1, le=10)


class BatchDownloadResponse(BaseModel):
    """Batch download response."""

    total: int
    success: int
    failed: int
    results: list[ImageDownloadResult]
