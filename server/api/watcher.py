"""Watcher API endpoints."""

from fastapi import APIRouter, Depends, HTTPException

from server.core.auth import require_auth
from server.core.container import get_watcher_service
from server.core.path_security import (
    PathSecurityError,
    validate_media_directory,
    validate_media_path,
)
from server.models.storage import is_p115_virtual_path
from server.models.watcher import (
    WatchedFolder,
    WatchedFolderCreate,
    WatchedFolderListResponse,
    WatchedFolderUpdate,
    WatcherStatusResponse,
)
from server.services.watcher_service import WatcherService

router = APIRouter(prefix="/api/watcher", tags=["watcher"], dependencies=[Depends(require_auth)])


def _validated_local_path(path: str, *, require_directory: bool) -> str:
    """Canonicalize one local watcher path inside configured media roots."""
    validator = validate_media_directory if require_directory else validate_media_path
    return str(validator(path))


def _validated_folder_create(folder: WatchedFolderCreate) -> WatchedFolderCreate:
    """Validate local paths before watcher state or persistence can change."""
    updates: dict[str, str] = {}
    if folder.provider == "local":
        updates["path"] = _validated_local_path(
            folder.path,
            require_directory=folder.enabled,
        )
    if folder.output_dir and not is_p115_virtual_path(folder.output_dir):
        updates["output_dir"] = _validated_local_path(
            folder.output_dir,
            require_directory=False,
        )
    return folder.model_copy(update=updates) if updates else folder


def _validated_folder_update(
    current: WatchedFolder,
    update: WatchedFolderUpdate,
) -> WatchedFolderUpdate:
    """Validate the merged watcher state and persist canonical local paths."""
    candidate_data = current.model_dump()
    candidate_data.update(update.model_dump(exclude_unset=True))
    candidate = WatchedFolder(**candidate_data)
    update_data = update.model_dump(exclude_unset=True)

    if candidate.provider == "local":
        update_data["path"] = _validated_local_path(
            candidate.path,
            require_directory=candidate.enabled,
        )
    if candidate.output_dir and not is_p115_virtual_path(candidate.output_dir):
        update_data["output_dir"] = _validated_local_path(
            candidate.output_dir,
            require_directory=False,
        )
    return WatchedFolderUpdate(**update_data)


@router.get("/status", response_model=WatcherStatusResponse)
async def get_status(
    watcher_service: WatcherService = Depends(get_watcher_service),
) -> WatcherStatusResponse:
    """Get watcher service status."""
    return await watcher_service.get_status()


@router.post("/start")
async def start_watcher(
    watcher_service: WatcherService = Depends(get_watcher_service),
) -> dict:
    """Start the watcher service."""
    try:
        await watcher_service.start()
    except PathSecurityError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"success": True, "message": "监控服务已启动"}


@router.post("/stop")
async def stop_watcher(
    watcher_service: WatcherService = Depends(get_watcher_service),
) -> dict:
    """Stop the watcher service."""
    await watcher_service.stop()
    return {"success": True, "message": "监控服务已停止"}


@router.get("/folders", response_model=WatchedFolderListResponse)
async def list_folders(
    watcher_service: WatcherService = Depends(get_watcher_service),
) -> WatchedFolderListResponse:
    """List all watched folders."""
    folders, total = await watcher_service.list_folders()
    return WatchedFolderListResponse(folders=folders, total=total)


@router.post("/folders", response_model=WatchedFolder)
async def create_folder(
    folder: WatchedFolderCreate,
    watcher_service: WatcherService = Depends(get_watcher_service),
) -> WatchedFolder:
    """Create a new watched folder."""
    try:
        validated = _validated_folder_create(folder)
    except PathSecurityError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return await watcher_service.create_folder(validated)


@router.get("/folders/{folder_id}", response_model=WatchedFolder)
async def get_folder(
    folder_id: str,
    watcher_service: WatcherService = Depends(get_watcher_service),
) -> WatchedFolder:
    """Get a watched folder by ID."""
    folder = await watcher_service.get_folder(folder_id)
    if folder is None:
        raise HTTPException(status_code=404, detail="Folder not found")
    return folder


@router.put("/folders/{folder_id}", response_model=WatchedFolder)
async def update_folder(
    folder_id: str,
    update: WatchedFolderUpdate,
    watcher_service: WatcherService = Depends(get_watcher_service),
) -> WatchedFolder:
    """Update a watched folder."""
    current = await watcher_service.get_folder(folder_id)
    if current is None:
        raise HTTPException(status_code=404, detail="Folder not found")
    try:
        validated = _validated_folder_update(current, update)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    folder = await watcher_service.update_folder(folder_id, validated)
    if folder is None:
        raise HTTPException(status_code=404, detail="Folder not found")
    return folder


@router.delete("/folders/{folder_id}")
async def delete_folder(
    folder_id: str,
    watcher_service: WatcherService = Depends(get_watcher_service),
) -> dict:
    """Delete a watched folder."""
    deleted = await watcher_service.delete_folder(folder_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Folder not found")
    return {"success": True, "message": "监控文件夹已删除"}
