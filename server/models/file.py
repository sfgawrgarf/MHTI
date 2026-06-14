"""File scanning data models."""

from pydantic import BaseModel

from server.models.storage import StorageLocator, StorageProvider


class ScannedFile(BaseModel):
    """Information about a scanned video file."""

    filename: str
    path: str
    size: int
    extension: str
    mtime: str | None = None  # 修改时间 ISO 格式
    # 115 网盘等 provider 文件的标识，本地文件为 None
    file_id: str | None = None
    parent_id: str | None = None


class ScanRequest(BaseModel):
    """Request model for folder scanning."""

    folder_path: str = ""
    locator: StorageLocator | None = None
    exclude_scraped: bool = True  # 默认排除已刮削的文件


class ScanResponse(BaseModel):
    """Response model for folder scanning."""

    folder_path: str
    total_files: int
    files: list[ScannedFile]
    scraped_count: int = 0  # 已刮削文件数量（被排除的）


class DirectoryEntry(BaseModel):
    """Information about a directory entry."""

    name: str
    path: str
    is_dir: bool
    provider: StorageProvider = StorageProvider.LOCAL
    file_id: str | None = None
    parent_id: str | None = None
    is_virtual: bool = False
    size: int | None = None  # 文件大小（字节），目录为 None
    mtime: str | None = None  # 修改时间 ISO 格式


class BrowseRequest(BaseModel):
    """Request model for directory browsing."""

    path: str = ""
    provider: StorageProvider = StorageProvider.LOCAL
    file_id: str | None = None
    page: int = 1
    page_size: int = 20


class BrowseResponse(BaseModel):
    """Response model for directory browsing."""

    current_path: str
    parent_path: str | None
    entries: list[DirectoryEntry]
    total: int = 0  # 总条目数
    page: int = 1
    page_size: int = 20
    # 115 网盘等 provider 的目录 file_id，用于前端返回上级时定位父目录
    current_file_id: str | None = None
    parent_file_id: str | None = None
