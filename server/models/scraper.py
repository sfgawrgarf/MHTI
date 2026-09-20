"""Scraper data models."""

from enum import Enum
from typing import Literal

from pydantic import BaseModel, model_validator

from server.models.emby import ConflictCheckResult
from server.models.history import ScrapeLogStep
from server.models.manual_job import ManualJobAdvancedSettings
from server.models.organize import OrganizeMode
from server.models.storage import (
    StorageLocator,
    infer_directory_locator,
    normalize_file_locator,
    validate_storage_capabilities,
)
from server.models.tmdb import TMDBSearchResult, TMDBSeries, TMDBEpisode


class _StorageValidatedScrapeRequest(BaseModel):
    """Shared provider validation for direct and queued scrape requests."""

    @model_validator(mode="after")
    def validate_storage_selection(self) -> "_StorageValidatedScrapeRequest":
        self.file_locator = normalize_file_locator(  # type: ignore[attr-defined]
            self.file_path, self.file_locator  # type: ignore[attr-defined]
        )
        self.output_locator = infer_directory_locator(  # type: ignore[attr-defined]
            self.output_dir, self.output_locator  # type: ignore[attr-defined]
        )
        self.metadata_locator = infer_directory_locator(  # type: ignore[attr-defined]
            self.metadata_dir, self.metadata_locator  # type: ignore[attr-defined]
        )
        validate_storage_capabilities(
            source_path=self.file_path,  # type: ignore[attr-defined]
            source_locator=self.file_locator,  # type: ignore[attr-defined]
            target_path=self.output_dir,  # type: ignore[attr-defined]
            target_locator=self.output_locator,  # type: ignore[attr-defined]
            metadata_locator=self.metadata_locator,  # type: ignore[attr-defined]
            allow_local_output=self.allow_local_output,  # type: ignore[attr-defined]
            organize_mode=self.link_mode,  # type: ignore[attr-defined]
        )
        return self


class ScrapeStatus(str, Enum):
    """Scrape operation status."""

    SUCCESS = "success"
    SEARCH_FAILED = "search_failed"
    API_FAILED = "api_failed"
    MOVE_FAILED = "move_failed"
    NFO_FAILED = "nfo_failed"
    NO_MATCH = "no_match"
    NEED_SELECTION = "need_selection"  # 需要用户选择剧集
    NEED_SEASON_EPISODE = "need_season_episode"  # 需要用户输入季/集
    FILE_CONFLICT = "file_conflict"  # 目标文件已存在
    EMBY_CONFLICT = "emby_conflict"  # Emby 媒体库冲突


class ScrapeRequest(_StorageValidatedScrapeRequest):
    """Request for scraping a single file."""

    file_path: str
    output_dir: str | None = None  # 视频输出目录
    metadata_dir: str | None = None  # 元数据输出目录（NFO、图片）
    file_locator: StorageLocator | None = None
    output_locator: StorageLocator | None = None
    metadata_locator: StorageLocator | None = None
    allow_local_output: bool = False
    link_mode: OrganizeMode | None = None  # 整理模式
    auto_select: bool = True  # 自动选择最佳匹配
    advanced_settings: ManualJobAdvancedSettings | None = None  # 高级设置


class ScrapeByIdRequest(_StorageValidatedScrapeRequest):
    """Request for scraping with manual TMDB ID."""

    file_path: str
    tmdb_id: int
    season: int
    episode: int
    output_dir: str | None = None  # 视频输出目录
    metadata_dir: str | None = None  # 元数据输出目录（NFO、图片）
    file_locator: StorageLocator | None = None
    output_locator: StorageLocator | None = None
    metadata_locator: StorageLocator | None = None
    allow_local_output: bool = False
    link_mode: OrganizeMode | None = None  # 整理模式
    skip_emby_check: bool = False  # 跳过 Emby 冲突检查
    file_action: Literal["overwrite", "rename"] | None = None
    advanced_settings: ManualJobAdvancedSettings | None = None  # 高级设置


class ScrapeResult(BaseModel):
    """Result of a scrape operation."""

    file_path: str
    status: ScrapeStatus
    message: str | None = None
    # 解析结果
    parsed_title: str | None = None
    parsed_season: int | None = None
    parsed_episode: int | None = None
    # 搜索结果
    search_results: list[TMDBSearchResult] | None = None
    selected_id: int | None = None
    # 元数据
    series_info: TMDBSeries | None = None
    episode_info: TMDBEpisode | None = None  # 集信息
    # 移动结果
    dest_path: str | None = None
    nfo_path: str | None = None
    # Emby 冲突
    emby_conflict: ConflictCheckResult | None = None
    # 刮削日志
    scrape_logs: list[ScrapeLogStep] = []


class BatchScrapeRequest(BaseModel):
    """Request for batch scraping."""

    file_paths: list[str]
    output_dir: str | None = None
    auto_select: bool = True
    dry_run: bool = False  # 预览模式，不实际执行


class BatchScrapeResponse(BaseModel):
    """Response for batch scraping."""

    total: int
    success: int
    failed: int
    results: list[ScrapeResult]


class ScrapePreview(BaseModel):
    """Preview of scrape operation."""

    file_path: str
    parsed_title: str | None = None
    parsed_season: int | None = None
    parsed_episode: int | None = None
    search_results: list[TMDBSearchResult] | None = None
    suggested_dest: str | None = None
