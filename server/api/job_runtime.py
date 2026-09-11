"""Authenticated runtime observability endpoint for background jobs."""

from fastapi import APIRouter, Depends

from server.core.auth import require_auth
from server.models.job_runtime import JobRuntimeMetrics
from server.services.job_monitor_service import JobMonitorService

router = APIRouter(
    prefix="/api/job-runtime",
    tags=["job-runtime"],
    dependencies=[Depends(require_auth)],
)


def get_service() -> JobMonitorService:
    return JobMonitorService()


@router.get("", response_model=JobRuntimeMetrics)
async def get_runtime_metrics(
    service: JobMonitorService = Depends(get_service),
) -> JobRuntimeMetrics:
    """Return persisted counts plus live queue, worker and file-I/O use."""
    return await service.get_metrics()
