"""File scanning API routes.

异常处理：所有 FileSystemError 子类（FolderNotFoundError、InvalidFolderError、
PermissionDeniedError）由全局异常处理器统一处理，无需在 API 层手动捕获。
"""

from fastapi import APIRouter, Depends, Query

from server.core.auth import require_auth
from server.core.container import get_file_service, get_history_service
from server.core.exceptions import validation_error
from server.models.file import BrowseResponse, ScanRequest, ScanResponse
from server.models.storage import StorageProvider
from server.services.file_service import FileService
from server.services.fingerprint_service import calculate_fingerprint
from server.services.history_service import HistoryService

router = APIRouter(prefix="/api", tags=["files"], dependencies=[Depends(require_auth)])


@router.post("/scan", response_model=ScanResponse)
async def scan_folder(
    request: ScanRequest,
    file_service: FileService = Depends(get_file_service),
    history_service: HistoryService = Depends(get_history_service),
) -> ScanResponse:
    """
    Scan a folder for video files.

    Args:
        request: ScanRequest containing the folder path.
        file_service: Injected FileService instance.
        history_service: Injected HistoryService instance.

    Returns:
        ScanResponse with list of discovered video files.

    Raises:
        FolderNotFoundError: 文件夹不存在 (404)
        InvalidFolderError: 无效文件夹路径 (400)
        PermissionDeniedError: 权限被拒绝 (403)
    """
    is_p115 = bool(request.locator and request.locator.provider == StorageProvider.P115)

    if not request.folder_path.strip() and not is_p115:
        raise validation_error("folder_path 不能为空", field="folder_path")

    if is_p115:
        files = await file_service.scan_folder_async(
            request.locator.path or request.folder_path,
            locator=request.locator,
        )
        # 115 文件没有本地指纹，直接返回
        return ScanResponse(
            folder_path=request.locator.path or request.folder_path,
            total_files=len(files),
            files=files,
        )

    files = file_service.scan_folder(request.folder_path)

    # 计算文件指纹并过滤已刮削的文件
    fingerprint_map = {}  # path -> fingerprint
    for f in files:
        fp = calculate_fingerprint(f.path)
        if fp:
            fingerprint_map[f.path] = fp

    # 查询已存在的指纹
    existing_fps = await history_service.get_existing_fingerprints(
        list(fingerprint_map.values())
    )

    # 过滤掉已刮削的文件
    filtered_files = [
        f for f in files
        if fingerprint_map.get(f.path) not in existing_fps
    ]

    return ScanResponse(
        folder_path=request.folder_path,
        total_files=len(filtered_files),
        files=filtered_files,
    )


@router.get("/files/browse", response_model=BrowseResponse)
async def browse_directory(
    path: str = Query(default="", description="Directory path to browse"),
    provider: StorageProvider = Query(
        default=StorageProvider.LOCAL,
        description="Storage provider to browse",
    ),
    file_id: str | None = Query(default=None, description="Provider file id"),
    page: int = Query(default=1, ge=1, description="Page number"),
    page_size: int = Query(default=20, ge=1, le=100, description="Items per page"),
    file_service: FileService = Depends(get_file_service),
) -> BrowseResponse:
    """
    Browse a directory and list its contents.

    Args:
        path: Path to browse. Empty for root/drives.
        page: Page number (1-based).
        page_size: Number of items per page.
        file_service: Injected FileService instance.

    Returns:
        BrowseResponse with directory entries.

    Raises:
        FolderNotFoundError: 文件夹不存在 (404)
        InvalidFolderError: 无效文件夹路径 (400)
        PermissionDeniedError: 权限被拒绝 (403)
    """
    if provider == StorageProvider.P115:
        (
            current_path,
            parent_path,
            entries,
            total,
            current_file_id,
            parent_file_id,
        ) = await file_service.browse_directory_async(
            path=path,
            provider=provider,
            file_id=file_id,
            page=page,
            page_size=page_size,
        )
    else:
        (
            current_path,
            parent_path,
            entries,
            total,
            current_file_id,
            parent_file_id,
        ) = file_service.browse_directory(
            path=path,
            provider=provider,
            file_id=file_id,
            page=page,
            page_size=page_size,
        )
    return BrowseResponse(
        current_path=current_path,
        parent_path=parent_path,
        entries=entries,
        total=total,
        page=page,
        page_size=page_size,
        current_file_id=current_file_id,
        parent_file_id=parent_file_id,
    )
