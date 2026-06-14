"""History API endpoints."""

import asyncio
import json

from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from sse_starlette.sse import EventSourceResponse

from server.core.auth import require_auth
from server.core.container import get_history_service
from server.models.history import (
    ConflictType,
    HistoryRecord,
    HistoryRecordCreate,
    HistoryRecordDetail,
    HistoryListResponse,
    TaskStatus,
)
from server.services.history_service import HistoryService

router = APIRouter(prefix="/api/history", tags=["history"], dependencies=[Depends(require_auth)])


async def _restore_locators_from_scrape_job(record: HistoryRecord) -> dict:
    """从关联的 scrape_job 恢复 locator，用于重试/处理时定位 115 等云端文件。

    conflict_data 里没有保存 locator，但 scrape_jobs 表保留了完整的 locator。
    通过 record.scrape_job_id 查表恢复。
    """
    if not record.scrape_job_id:
        return {}
    try:
        from server.services.scrape_job_service import ScrapeJobService
        service = ScrapeJobService()
        job = await service.get_job(record.scrape_job_id)
        if job is None:
            return {}
        result = {
            "file_locator": job.file_locator,
            "output_locator": job.output_locator,
            "metadata_locator": job.metadata_locator,
            "allow_local_output": job.allow_local_output,
        }
        # 只保留非空值
        return {k: v for k, v in result.items() if v is not None and v is not False}
    except Exception:
        return {}


@router.get("", response_model=HistoryListResponse)
async def list_records(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    manual_job_id: int | None = Query(None),
    search: str | None = Query(None, description="搜索名称、文件夹"),
    status: TaskStatus | None = Query(None, description="状态筛选"),
    history_service: HistoryService = Depends(get_history_service),
) -> HistoryListResponse:
    """List history records with pagination, search and status filter."""
    records, total = await history_service.list_records(
        limit=limit,
        offset=offset,
        manual_job_id=manual_job_id,
        search=search,
        status=status,
    )
    return HistoryListResponse(records=records, total=total)


@router.post("", response_model=HistoryRecord)
async def create_record(
    record: HistoryRecordCreate,
    history_service: HistoryService = Depends(get_history_service),
) -> HistoryRecord:
    """Create a new history record."""
    return await history_service.create_record(record)


@router.get("/export")
async def export_records(
    history_service: HistoryService = Depends(get_history_service),
) -> PlainTextResponse:
    """Export history records as CSV."""
    csv_content = await history_service.export_csv()
    return PlainTextResponse(
        content=csv_content,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=history.csv"},
    )


@router.delete("")
async def clear_records(
    before_days: int | None = Query(None, ge=1, description="Clear records older than N days"),
    history_service: HistoryService = Depends(get_history_service),
) -> dict:
    """Clear history records."""
    deleted = await history_service.clear_records(before_days=before_days)
    return {"success": True, "deleted": deleted, "message": f"已删除 {deleted} 条记录"}


@router.get("/{record_id}", response_model=HistoryRecordDetail)
async def get_record(
    record_id: str,
    history_service: HistoryService = Depends(get_history_service),
) -> HistoryRecordDetail:
    """Get a history record by ID."""
    record = await history_service.get_record(record_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Record not found")
    return record


@router.get("/{record_id}/logs/stream")
async def stream_logs(
    record_id: str,
    history_service: HistoryService = Depends(get_history_service),
):
    """SSE 端点：实时推送刮削日志"""
    # 验证记录存在
    record = await history_service.get_record(record_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Record not found")

    async def event_generator():
        queue = await history_service.subscribe_logs(record_id)
        try:
            # 先发送当前日志
            if record.scrape_logs:
                yield {
                    "event": "logs",
                    "data": json.dumps(
                        [log.model_dump() for log in record.scrape_logs],
                        ensure_ascii=False
                    ),
                }

            # 持续监听更新
            while True:
                try:
                    logs = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield {
                        "event": "logs",
                        "data": json.dumps(
                            [log.model_dump() for log in logs],
                            ensure_ascii=False
                        ),
                    }
                except asyncio.TimeoutError:
                    # 发送心跳保持连接
                    yield {"event": "ping", "data": ""}
        except asyncio.CancelledError:
            pass
        finally:
            history_service.unsubscribe_logs(record_id, queue)

    return EventSourceResponse(event_generator())


@router.delete("/{record_id}")
async def delete_record(
    record_id: str,
    history_service: HistoryService = Depends(get_history_service),
) -> dict:
    """Delete a history record."""
    deleted = await history_service.delete_record(record_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Record not found")
    return {"success": True, "message": "记录已删除"}


class ResolveConflictRequest(BaseModel):
    """请求模型：处理冲突"""

    conflict_type: ConflictType
    # NEED_SELECTION: 选择的 TMDB ID
    tmdb_id: int | None = None
    # NEED_SEASON_EPISODE: 季/集号
    season: int | None = None
    episode: int | None = None
    # FILE_CONFLICT: 处理方式
    file_action: str | None = None  # "overwrite" | "skip" | "rename"


async def _execute_scrape_and_update(
    history_service: HistoryService,
    record_id: str,
    scrape_request,
    user_selection_log: str | None = None,
) -> dict:
    """执行刮削并更新记录状态（公共逻辑）"""
    from server.core.container import get_scraper_service
    from server.services.manual_job_service import ManualJobService
    from server.models.manual_job import ManualJobStatus
    from server.models.history import ScrapeLogStep, ScrapeLogEntry

    scraper = get_scraper_service()

    # 获取原有日志和 manual_job_id
    record = await history_service.get_record(record_id)
    existing_logs = list(record.scrape_logs) if record and record.scrape_logs else []
    manual_job_id = record.manual_job_id if record else None

    # 如果有用户选择日志，添加到原有日志后
    if user_selection_log:
        user_log = ScrapeLogStep(
            name="用户手动选择",
            completed=True,
            logs=[ScrapeLogEntry(message=user_selection_log)],
        )
        existing_logs.append(user_log)
        await history_service.update_scrape_logs(record_id, existing_logs)

    # 创建日志回调
    async def on_log_update(logs):
        # 将新日志追加到原有日志后
        combined_logs = existing_logs + logs
        await history_service.update_scrape_logs(record_id, combined_logs)

    result = await scraper.scrape_by_id(scrape_request, on_log_update=on_log_update)

    # 清理日志缓存
    history_service.clear_log_cache(record_id)

    if result.status.value == "success":
        series = result.series_info
        episode = result.episode_info
        await history_service.update_record(
            record_id,
            status=TaskStatus.SUCCESS,
            error_message=None,
            title=series.name if series else None,
            original_title=series.original_name if series else None,
            plot=series.overview if series else None,
            poster_url=f"https://image.tmdb.org/t/p/w500{series.poster_path}" if series and series.poster_path else None,
            release_date=str(series.first_air_date) if series and series.first_air_date else None,
            rating=series.vote_average if series else None,
            tags=series.genres if series else None,
            season_number=result.parsed_season,
            episode_number=result.parsed_episode,
            episode_title=episode.name if episode else None,
            episode_overview=episode.overview if episode else None,
            episode_still_url=f"https://image.tmdb.org/t/p/w500{episode.still_path}" if episode and episode.still_path else None,
            episode_air_date=str(episode.air_date) if episode and episode.air_date else None,
        )

        # 更新手动任务统计（skip_count - 1, success_count + 1）
        if manual_job_id:
            job_service = ManualJobService()
            job = await job_service.get_job(manual_job_id)
            if job:
                await job_service.update_job_status(
                    manual_job_id,
                    job.status,  # 保持原状态
                    success_count=job.success_count + 1,
                    skip_count=max(0, job.skip_count - 1),
                )

        return {"success": True, "message": "处理成功", "dest_path": result.dest_path}
    else:
        await history_service.update_record(
            record_id, status=TaskStatus.FAILED, error_message=result.message
        )
        raise HTTPException(status_code=400, detail=result.message)


@router.put("/{record_id}/resolve")
async def resolve_conflict(
    record_id: str,
    request: ResolveConflictRequest,
    history_service: HistoryService = Depends(get_history_service),
) -> dict:
    """处理待处理的冲突记录"""
    from server.models.scraper import ScrapeByIdRequest

    # 获取记录
    record = await history_service.get_record(record_id)
    if record is None:
        raise HTTPException(status_code=404, detail="记录不存在")

    if record.status != TaskStatus.PENDING_ACTION:
        raise HTTPException(status_code=400, detail="该记录不需要处理")

    if record.conflict_type != request.conflict_type:
        raise HTTPException(status_code=400, detail="冲突类型不匹配")

    output_dir = record.conflict_data.get("output_dir") if record.conflict_data else None
    metadata_dir = record.conflict_data.get("metadata_dir") if record.conflict_data else None
    # 从 conflict_data 恢复 link_mode
    link_mode_value = record.conflict_data.get("link_mode") if record.conflict_data else None
    from server.models.organize import OrganizeMode
    link_mode = OrganizeMode(link_mode_value) if link_mode_value else None

    # 恢复 locator（支持 115 等云端文件重试）
    locators = await _restore_locators_from_scrape_job(record)

    # 根据冲突类型处理
    if request.conflict_type == ConflictType.NEED_SELECTION:
        if request.tmdb_id is None:
            raise HTTPException(status_code=400, detail="请选择 TMDB ID")
        if request.season is None or request.episode is None:
            raise HTTPException(status_code=400, detail="请提供季/集号")

        # 获取选中剧集名称
        selected_name = f"TMDB ID: {request.tmdb_id}"
        if record.conflict_data and record.conflict_data.get("search_results"):
            for r in record.conflict_data["search_results"]:
                if r.get("id") == request.tmdb_id:
                    selected_name = r.get("name", selected_name)
                    break

        user_log = f"用户选择了「{selected_name}」S{request.season:02d}E{request.episode:02d}"

        scrape_request = ScrapeByIdRequest(
            file_path=record.folder_path,
            tmdb_id=request.tmdb_id,
            season=request.season,
            episode=request.episode,
            output_dir=output_dir,
            metadata_dir=metadata_dir,
            link_mode=link_mode,
            **locators,
        )
        return await _execute_scrape_and_update(history_service, record_id, scrape_request, user_log)

    elif request.conflict_type == ConflictType.NEED_SEASON_EPISODE:
        if request.season is None or request.episode is None:
            raise HTTPException(status_code=400, detail="请提供季/集号")

        tmdb_id = record.conflict_data.get("tmdb_id") if record.conflict_data else None
        if tmdb_id is None:
            raise HTTPException(status_code=400, detail="缺少 TMDB ID")

        user_log = f"用户选择了 S{request.season:02d}E{request.episode:02d}"

        scrape_request = ScrapeByIdRequest(
            file_path=record.folder_path,
            tmdb_id=tmdb_id,
            season=request.season,
            episode=request.episode,
            output_dir=output_dir,
            metadata_dir=metadata_dir,
            link_mode=link_mode,
            **locators,
        )
        return await _execute_scrape_and_update(history_service, record_id, scrape_request, user_log)

    elif request.conflict_type == ConflictType.FILE_CONFLICT:
        if request.file_action not in ("overwrite", "skip", "rename"):
            raise HTTPException(status_code=400, detail="无效的处理方式")

        if request.file_action == "skip":
            await history_service.update_record(
                record_id, status=TaskStatus.SKIPPED, error_message="用户跳过"
            )
            return {"success": True, "message": "已跳过"}

        tmdb_id = record.conflict_data.get("tmdb_id") if record.conflict_data else None
        if tmdb_id is None:
            raise HTTPException(status_code=400, detail="缺少 TMDB ID")

        action_text = "覆盖" if request.file_action == "overwrite" else "重命名"
        user_log = f"用户选择了{action_text}文件"

        scrape_request = ScrapeByIdRequest(
            file_path=record.folder_path,
            tmdb_id=tmdb_id,
            season=record.conflict_data.get("season", 1) if record.conflict_data else 1,
            episode=record.conflict_data.get("episode", 1) if record.conflict_data else 1,
            output_dir=output_dir,
            metadata_dir=metadata_dir,
            link_mode=link_mode,
            **locators,
        )
        return await _execute_scrape_and_update(history_service, record_id, scrape_request, user_log)

    elif request.conflict_type in (ConflictType.NO_MATCH, ConflictType.SEARCH_FAILED, ConflictType.API_FAILED):
        # 手动输入 TMDB ID 的情况
        if request.tmdb_id is None:
            raise HTTPException(status_code=400, detail="请输入 TMDB ID")
        if request.season is None or request.episode is None:
            raise HTTPException(status_code=400, detail="请提供季/集号")

        user_log = f"用户手动输入 TMDB ID: {request.tmdb_id}, S{request.season:02d}E{request.episode:02d}"

        scrape_request = ScrapeByIdRequest(
            file_path=record.folder_path,
            tmdb_id=request.tmdb_id,
            season=request.season,
            episode=request.episode,
            output_dir=output_dir,
            metadata_dir=metadata_dir,
            link_mode=link_mode,
            **locators,
        )
        return await _execute_scrape_and_update(history_service, record_id, scrape_request, user_log)

    elif request.conflict_type == ConflictType.EMBY_CONFLICT:
        # Emby 冲突处理
        if request.file_action == "skip":
            await history_service.update_record(
                record_id, status=TaskStatus.SKIPPED, error_message="用户跳过（Emby 已存在）"
            )
            return {"success": True, "message": "已跳过"}

        tmdb_id = record.conflict_data.get("tmdb_id") if record.conflict_data else None
        if tmdb_id is None:
            raise HTTPException(status_code=400, detail="缺少 TMDB ID")

        # 获取季/集号（用户可能选择了其他季/集）
        season = request.season if request.season is not None else (record.conflict_data.get("season", 1) if record.conflict_data else 1)
        episode = request.episode if request.episode is not None else (record.conflict_data.get("episode", 1) if record.conflict_data else 1)

        if request.file_action == "force":
            user_log = f"用户强制继续刮削 S{season:02d}E{episode:02d}（忽略 Emby 冲突）"
        else:
            user_log = f"用户选择刮削为 S{season:02d}E{episode:02d}"

        scrape_request = ScrapeByIdRequest(
            file_path=record.folder_path,
            tmdb_id=tmdb_id,
            season=season,
            episode=episode,
            output_dir=output_dir,
            metadata_dir=metadata_dir,
            link_mode=link_mode,
            skip_emby_check=True,  # 跳过 Emby 检查
        )
        return await _execute_scrape_and_update(history_service, record_id, scrape_request, user_log)

    raise HTTPException(status_code=400, detail="未知的冲突类型")


class RetryRequest(BaseModel):
    """请求模型：重试刮削"""

    tmdb_id: int  # TMDB ID
    season: int  # 季号
    episode: int  # 集号


@router.post("/{record_id}/retry")
async def retry_scrape(
    record_id: str,
    request: RetryRequest,
    history_service: HistoryService = Depends(get_history_service),
) -> dict:
    """重试失败的刮削记录

    允许对 failed/timeout/cancelled 状态的记录重新执行刮削。
    """
    from server.models.scraper import ScrapeByIdRequest
    from server.models.organize import OrganizeMode

    # 1. 获取并验证记录
    record = await history_service.get_record(record_id)
    if record is None:
        raise HTTPException(status_code=404, detail="记录不存在")

    # 2. 验证状态允许重试
    retryable_statuses = [TaskStatus.FAILED, TaskStatus.TIMEOUT, TaskStatus.CANCELLED]
    if record.status not in retryable_statuses:
        raise HTTPException(
            status_code=400,
            detail=f"该记录状态为 {record.status.value}，不支持重试。仅支持 failed/timeout/cancelled 状态",
        )

    # 3. 从 conflict_data 恢复原始参数
    conflict_data = record.conflict_data or {}
    output_dir = conflict_data.get("output_dir")
    metadata_dir = conflict_data.get("metadata_dir")
    link_mode_value = conflict_data.get("link_mode")
    link_mode = OrganizeMode(link_mode_value) if link_mode_value else None

    # 4. 构建刮削请求（恢复 locator 以支持 115 等云端文件）
    user_log = f"用户手动重试: TMDB ID {request.tmdb_id}, S{request.season:02d}E{request.episode:02d}"
    locators = await _restore_locators_from_scrape_job(record)

    scrape_request = ScrapeByIdRequest(
        file_path=record.folder_path,
        tmdb_id=request.tmdb_id,
        season=request.season,
        episode=request.episode,
        output_dir=output_dir,
        metadata_dir=metadata_dir,
        link_mode=link_mode,
        **locators,
    )

    # 5. 执行刮削
    return await _execute_scrape_and_update(
        history_service, record_id, scrape_request, user_log
    )
