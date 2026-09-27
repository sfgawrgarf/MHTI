"""Data models for subtitle processing."""

from enum import Enum
from pathlib import Path

from pydantic import BaseModel, Field, field_validator


class SubtitleLanguage(str, Enum):
    """Subtitle language identifiers."""

    CHS = "chs"  # Simplified Chinese
    CHT = "cht"  # Traditional Chinese
    ENG = "eng"  # English
    JPN = "jpn"  # Japanese
    KOR = "kor"  # Korean
    UNKNOWN = ""  # Unknown


class SubtitleFile(BaseModel):
    """Subtitle file information."""

    path: str
    filename: str
    extension: str
    language: SubtitleLanguage | None = None
    associated_video: str | None = None


class SubtitleScanRequest(BaseModel):
    """Request to scan for subtitles."""

    folder_path: str


class SubtitleScanResponse(BaseModel):
    """Response with scanned subtitles."""

    subtitles: list[SubtitleFile]
    total: int


class SubtitleAssociateRequest(BaseModel):
    """Request to associate subtitles with videos."""

    folder_path: str
    video_files: list[str] | None = None  # If None, auto-detect


class VideoSubtitleAssociation(BaseModel):
    """Association between a video and its subtitles."""

    video: str
    video_path: str
    subtitles: list[SubtitleFile]


class SubtitleAssociateResponse(BaseModel):
    """Response with video-subtitle associations."""

    associations: list[VideoSubtitleAssociation]


class SubtitleRenameRequest(BaseModel):
    """Request to rename subtitle file."""

    subtitle_path: str
    new_video_name: str = Field(min_length=1, max_length=255)
    preserve_language: bool = True

    @field_validator("new_video_name")
    @classmethod
    def validate_new_video_name(cls, value: str) -> str:
        """Keep the new name in the subtitle's current directory."""
        if "\x00" in value or value in {".", ".."} or Path(value).name != value:
            raise ValueError("new_video_name 必须是单个文件名")
        return value


class SubtitleRenameResult(BaseModel):
    """Result of subtitle rename."""

    source_path: str
    dest_path: str
    success: bool
    error: str | None = None


class BatchSubtitleRenameRequest(BaseModel):
    """Batch subtitle rename request."""

    items: list[SubtitleRenameRequest] = Field(default_factory=list, max_length=100)


class BatchSubtitleRenameResponse(BaseModel):
    """Batch subtitle rename response."""

    total: int
    success: int
    failed: int
    results: list[SubtitleRenameResult]
