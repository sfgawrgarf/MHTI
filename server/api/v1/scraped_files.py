"""已刮削文件登记的查询与清理接口。"""

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from server.api.deps import get_scraped_file_service, require_auth
from server.application.scraped_file_service import ScrapedFileService
from server.models.scraped_file import ScrapedFile


router = APIRouter(
    prefix="/api/scraped-files",
    tags=["scraped-files"],
    dependencies=[Depends(require_auth)],
)


class ScrapedFileListResponse(BaseModel):
    """分页的已刮削文件登记。"""

    records: list[ScrapedFile]
    total: int


class DeleteResponse(BaseModel):
    """删除数量。"""

    deleted: int


@router.get("", response_model=ScrapedFileListResponse)
async def list_scraped_files(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    search: str | None = Query(default=None),
    service: ScrapedFileService = Depends(get_scraped_file_service),
) -> ScrapedFileListResponse:
    """获取已刮削文件登记。"""
    records, total = await service.list_records(
        limit=page_size,
        offset=(page - 1) * page_size,
        search=search,
    )
    return ScrapedFileListResponse(records=records, total=total)


@router.delete("", response_model=DeleteResponse)
async def delete_scraped_files(
    ids: list[str] = Query(..., max_length=500, description="要删除的记录 ID"),
    service: ScrapedFileService = Depends(get_scraped_file_service),
) -> DeleteResponse:
    """删除登记，不删除实际文件。"""
    return DeleteResponse(deleted=await service.delete_records(ids))


@router.delete("/by-paths", response_model=DeleteResponse)
async def delete_by_paths(
    paths: list[str] = Query(..., max_length=500, description="要匹配的文件路径"),
    service: ScrapedFileService = Depends(get_scraped_file_service),
) -> DeleteResponse:
    """按源路径或产物路径删除登记，不删除实际文件。"""
    return DeleteResponse(deleted=await service.delete_by_paths(paths))


@router.delete("/clear", response_model=DeleteResponse)
async def clear_all(
    service: ScrapedFileService = Depends(get_scraped_file_service),
) -> DeleteResponse:
    """清空登记，不删除实际文件。"""
    return DeleteResponse(deleted=await service.clear_all())


@router.get("/check")
async def check_scraped(
    path: str = Query(..., description="源文件路径"),
    service: ScrapedFileService = Depends(get_scraped_file_service),
) -> dict:
    """检查源文件是否已有登记。"""
    record = await service.get_record(path)
    return {
        "is_scraped": record is not None,
        "record": record.model_dump(mode="json") if record else None,
    }
