"""Manual job data models."""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, model_validator
from server.models.storage import (
    StorageLocator,
    infer_directory_locator,
    is_p115_to_local,
    normalize_file_locator,
    validate_storage_capabilities,
)


class ManualJobStatus(str, Enum):
    """Manual job status."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"


class LinkMode(int, Enum):
    """File organization mode."""

    HARDLINK = 1
    MOVE = 2
    COPY = 3
    SYMLINK = 4


class JobSource(str, Enum):
    """任务来源类型"""

    MANUAL = "manual"  # 手动创建
    WATCHER = "watcher"  # 文件监控触发


class ManualJobAdvancedSettings(BaseModel):
    """手动任务高级设置 - 分类全局配置开关"""

    # 各分类的全局配置开关
    use_global_organize: bool = True
    use_global_download: bool = True
    use_global_naming: bool = True
    use_global_metadata: bool = True

    # 整理设置（当 use_global_organize=False 时使用）
    scan_filters_enabled: bool = False
    metadata_folder: str = ""
    file_size_filter: int = Field(100, ge=0)
    file_ext_whitelist: list[str] = Field(default_factory=list, max_length=200)
    file_name_blacklist: list[str] = Field(default_factory=list, max_length=200)
    file_sanitize_list: list[str] = Field(default_factory=list, max_length=200)
    delete_metadata_on_fail: bool = False
    overwrite_video: bool = False
    overwrite_image: bool = False
    protect_ext_whitelist: bool = False
    delete_by_size: bool = False
    delete_by_ext: bool = False
    delete_by_name: bool = False
    extra_ext_whitelist: list[str] = Field(default_factory=list, max_length=200)

    # 下载设置（当 use_global_download=False 时使用）
    download_poster: bool = True
    download_thumb: bool = True
    download_fanart: bool = False

    # 命名设置（当 use_global_naming=False 时使用）
    series_folder_template: str = ""
    season_folder_template: str = ""
    episode_file_template: str = ""

    # 元数据设置（当 use_global_metadata=False 时使用）
    scrape_title: bool = True
    scrape_plot: bool = True
    nfo_enabled: bool = True
    process_subtitle: bool = True


class ManualJob(BaseModel):
    """Manual job record."""

    id: int
    scan_path: str
    target_folder: str
    metadata_dir: str = ""  # 元数据目录
    scan_locator: StorageLocator | None = None
    target_locator: StorageLocator | None = None
    metadata_locator: StorageLocator | None = None
    allow_local_output: bool = False
    link_mode: LinkMode
    delete_empty_parent: bool = True
    config_reuse_id: int | None = None
    source: JobSource = JobSource.MANUAL  # 任务来源
    advanced_settings: ManualJobAdvancedSettings | None = None  # 高级设置
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    status: ManualJobStatus = ManualJobStatus.PENDING
    success_count: int = 0
    skip_count: int = 0
    error_count: int = 0
    total_count: int = 0
    error_message: str | None = None
    child_pending_count: int = 0
    child_running_count: int = 0
    child_pending_action_count: int = 0


class ManualJobCreate(BaseModel):
    """Request for creating a manual job."""

    scan_path: str
    target_folder: str
    metadata_dir: str = ""  # 元数据目录
    scan_locator: StorageLocator | None = None
    target_locator: StorageLocator | None = None
    metadata_locator: StorageLocator | None = None
    allow_local_output: bool = False
    link_mode: LinkMode = LinkMode.MOVE
    delete_empty_parent: bool = True
    config_reuse_id: int | None = None
    source: JobSource = JobSource.MANUAL  # 任务来源
    advanced_settings: ManualJobAdvancedSettings | None = None  # 高级设置

    @model_validator(mode="after")
    def validate_storage_selection(self) -> "ManualJobCreate":
        """Normalize plain paths and reject unsupported provider combinations."""
        self._validate_advanced_settings()
        if (
            not self.metadata_dir.strip()
            and self.advanced_settings is not None
            and not self.advanced_settings.use_global_organize
            and self.advanced_settings.metadata_folder.strip()
        ):
            # Preserve old saved task settings without keeping two competing
            # metadata-directory controls in the UI.
            self.metadata_dir = self.advanced_settings.metadata_folder.strip()
        self.scan_locator = infer_directory_locator(
            self.scan_path, self.scan_locator, allow_file=True
        )
        if self.scan_locator is not None and not self.scan_locator.is_dir:
            self.scan_locator = normalize_file_locator(
                self.scan_path, self.scan_locator
            )
        self.target_locator = infer_directory_locator(
            self.target_folder, self.target_locator
        )
        self.metadata_locator = infer_directory_locator(
            self.metadata_dir or None, self.metadata_locator
        )
        if (
            is_p115_to_local(
                source_path=self.scan_path,
                source_locator=self.scan_locator,
                target_path=self.target_folder,
                target_locator=self.target_locator,
            )
            and self.link_mode == LinkMode.MOVE
        ):
            self.link_mode = LinkMode.COPY
        validate_storage_capabilities(
            source_path=self.scan_path,
            source_locator=self.scan_locator,
            target_path=self.target_folder,
            target_locator=self.target_locator,
            metadata_locator=self.metadata_locator,
            allow_local_output=self.allow_local_output,
            organize_mode=self.link_mode,
        )
        return self

    def _validate_advanced_settings(self) -> None:
        """Reject settings that the runtime cannot honor safely."""
        settings = self.advanced_settings
        if settings is None:
            return

        unsupported: list[str] = []
        if not settings.use_global_organize:
            if settings.delete_metadata_on_fail:
                unsupported.append("delete_metadata_on_fail")
            if settings.file_sanitize_list:
                unsupported.append("file_sanitize_list")
            if settings.protect_ext_whitelist:
                unsupported.append("protect_ext_whitelist")
            if settings.delete_by_size:
                unsupported.append("delete_by_size")
            if settings.delete_by_ext:
                unsupported.append("delete_by_ext")
            if settings.delete_by_name:
                unsupported.append("delete_by_name")
        if not settings.use_global_metadata:
            if not settings.scrape_title:
                unsupported.append("scrape_title=false")
            if not settings.scrape_plot:
                unsupported.append("scrape_plot=false")
        if unsupported:
            raise ValueError(
                "以下高级设置尚不支持，为避免静默忽略已拒绝创建任务: "
                + ", ".join(unsupported)
            )

        from server.core.media_extensions import normalize_video_extensions

        extensions = normalize_video_extensions(
            settings.file_ext_whitelist + settings.extra_ext_whitelist
        )
        if any(
            len(extension) > 32 or "/" in extension or "\\" in extension
            for extension in extensions
        ):
            raise ValueError("文件扩展名格式无效")

        if not settings.use_global_naming:
            from server.models.template import NamingTemplate
            from server.services.template_service import TemplateService

            defaults = NamingTemplate()
            templates = {
                "剧集文件夹": settings.series_folder_template.strip()
                or defaults.series_folder,
                "季文件夹": settings.season_folder_template.strip()
                or defaults.season_folder,
                "剧集文件": settings.episode_file_template.strip()
                or defaults.episode_file,
            }
            validator = TemplateService()
            for label, template in templates.items():
                result = validator.validate_template(template)
                if not result.valid:
                    raise ValueError(f"{label}模板无效: {result.error}")


class ManualJobListResponse(BaseModel):
    """Response for listing manual jobs."""

    jobs: list[ManualJob]
    total: int


class ManualJobDeleteRequest(BaseModel):
    """Request for deleting manual jobs."""

    ids: list[int]
