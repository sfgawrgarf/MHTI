"""后台任务与文件 I/O 运行时观测接口。"""

from fastapi import APIRouter, Depends

from server.api.deps import get_job_monitor_service, require_auth
from server.application.job_monitor_service import JobMonitorService
from server.models.job_runtime import JobRuntimeMetrics


router = APIRouter(
    prefix="/api/job-runtime",
    tags=["job-runtime"],
    dependencies=[Depends(require_auth)],
)


@router.get("", response_model=JobRuntimeMetrics)
async def get_runtime_metrics(
    service: JobMonitorService = Depends(get_job_monitor_service),
) -> JobRuntimeMetrics:
    """返回任务队列和文件 I/O 的运行时状态。"""
    return await service.get_metrics()
