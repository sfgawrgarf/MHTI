"""Scraped files API routes - 已刮削文件管理"""

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from server.core.auth import require_auth
from server.models.scraped_file import ScrapedFile
from server.services.scraped_file_service import ScrapedFileService

router = APIRouter(
    prefix="/api/scraped-files",
    tags=["scraped-files"],
    dependencies=[Depends(require_auth)],
)


class ScrapedFileListResponse(BaseModel):
    """已刮削文件列表响应"""
    records: list[ScrapedFile]
    total: int


class DeleteResponse(BaseModel):
    """删除响应"""
    deleted: int


def get_service() -> ScrapedFileService:
    return ScrapedFileService()


@router.get("", response_model=ScrapedFileListResponse)
async def list_scraped_files(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    search: str | None = Query(default=None),
    service: ScrapedFileService = Depends(get_service),
) -> ScrapedFileListResponse:
    """获取已刮削文件列表"""
    offset = (page - 1) * page_size
    records, total = await service.list_records(
        limit=page_size,
        offset=offset,
        search=search,
    )
    return ScrapedFileListResponse(records=records, total=total)


@router.delete("", response_model=DeleteResponse)
async def delete_scraped_files(
    ids: list[str] = Query(..., max_length=500, description="要删除的记录ID列表"),
    service: ScrapedFileService = Depends(get_service),
) -> DeleteResponse:
    """删除已刮削文件记录（允许文件重新刮削）"""
    deleted = await service.delete_records(ids)
    return DeleteResponse(deleted=deleted)


@router.delete("/by-paths", response_model=DeleteResponse)
async def delete_by_paths(
    paths: list[str] = Query(..., max_length=500, description="要删除的文件路径列表"),
    service: ScrapedFileService = Depends(get_service),
) -> DeleteResponse:
    """根据文件路径删除记录"""
    deleted = await service.delete_by_paths(paths)
    return DeleteResponse(deleted=deleted)


@router.delete("/clear", response_model=DeleteResponse)
async def clear_all(
    service: ScrapedFileService = Depends(get_service),
) -> DeleteResponse:
    """清空所有已刮削文件记录"""
    deleted = await service.clear_all()
    return DeleteResponse(deleted=deleted)


@router.get("/check")
async def check_scraped(
    path: str = Query(..., description="文件路径"),
    service: ScrapedFileService = Depends(get_service),
) -> dict:
    """检查文件是否已刮削"""
    is_scraped = await service.is_scraped(path)
    record = await service.get_record(path) if is_scraped else None
    return {
        "is_scraped": is_scraped,
        "record": record.model_dump(mode="json") if record else None,
    }
