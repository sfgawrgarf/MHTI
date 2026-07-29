"""Manual job API endpoints."""
from server.core.auth import require_auth

from fastapi import APIRouter, Depends, HTTPException, Query

from server.models.manual_job import (
    ManualJob,
    ManualJobCreate,
    ManualJobDeleteRequest,
    ManualJobListResponse,
    ManualJobStatus,
)
from server.services.manual_job_service import ManualJobService

router = APIRouter(prefix="/api/manual-jobs", tags=["manual-jobs"], dependencies=[Depends(require_auth)])


def get_service() -> ManualJobService:
    """Dependency injection for ManualJobService."""
    return ManualJobService()


@router.post("", response_model=ManualJob)
async def create_job(
    job: ManualJobCreate,
    service: ManualJobService = Depends(get_service),
) -> ManualJob:
    """Create a new manual job."""
    return await service.create_job(job)


@router.get("", response_model=ManualJobListResponse)
async def list_jobs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: str | None = Query(None),
    status: ManualJobStatus | None = Query(None),
    service: ManualJobService = Depends(get_service),
) -> ManualJobListResponse:
    """List manual jobs with pagination."""
    offset = (page - 1) * page_size
    jobs, total = await service.list_jobs(
        limit=page_size,
        offset=offset,
        search=search,
        status=status,
    )
    return ManualJobListResponse(jobs=jobs, total=total)


@router.get("/{job_id}", response_model=ManualJob)
async def get_job(
    job_id: int,
    service: ManualJobService = Depends(get_service),
) -> ManualJob:
    """Get a manual job by ID."""
    job = await service.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="手动任务不存在")
    return job


@router.delete("")
async def delete_jobs(
    request: ManualJobDeleteRequest,
    service: ManualJobService = Depends(get_service),
) -> dict:
    """Delete manual jobs by IDs."""
    deleted = await service.delete_jobs(request.ids)
    return {"deleted": deleted}
