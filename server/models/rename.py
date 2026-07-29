"""Data models for file renaming operations."""

from typing import Literal

from pydantic import BaseModel, Field

from server.models.organize import OrganizeMode


class RenameRequest(BaseModel):
    """Single file rename request."""

    source_path: str
    title: str
    season: int
    episode: int
    episode_title: str | None = None
    year: int | None = None
    output_dir: str | None = None  # If None, rename in place
    link_mode: OrganizeMode | None = None  # 整理模式：copy/move/hardlink/symlink
    conflict_action: Literal["overwrite", "rename"] | None = None


class RenameResult(BaseModel):
    """Result of a rename operation."""

    source_path: str
    dest_path: str
    success: bool
    error: str | None = None
    backup_path: str | None = None


class RenamePreview(BaseModel):
    """Preview of rename operation (dry-run)."""

    source_path: str
    dest_path: str
    dest_folder: str
    new_filename: str
    will_create_dirs: list[str] = Field(default_factory=list)


class BatchRenameRequest(BaseModel):
    """Batch rename request."""

    items: list[RenameRequest] = Field(default_factory=list, max_length=100)
    create_backup: bool = False
    dry_run: bool = False


class BatchRenameResponse(BaseModel):
    """Batch rename response."""

    total: int
    success: int
    failed: int
    results: list[RenameResult]
    previews: list[RenamePreview] | None = None
