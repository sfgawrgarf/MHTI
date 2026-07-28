"""History API endpoints."""

import asyncio
import json
from pathlib import Path

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


class AIRetryRequest(BaseModel):
    """Batch retry only unresolved no-match records through the AI pipeline."""

    record_ids: list[str] | None = None
    limit: int = 100


@router.post("/ai-retry")
async def retry_no_match_with_ai(
    request: AIRetryRequest,
    history_service: HistoryService = Depends(get_history_service),
) -> dict:
    """Queue fresh replacement jobs for pending ``no_match`` records.

    The worker performs the normal AI-assisted scrape; this endpoint never
    writes an AI suggestion into an old record and never reuses its job ID.
    """
    from server.services.scrape_job_service import ScrapeJobService

    if request.limit < 1 or request.limit > 500:
        raise HTTPException(status_code=400, detail="limit 必须在 1 到 500 之间")

    if request.record_ids:
        candidate_ids = request.record_ids[:request.limit]
    else:
        records, _ = await history_service.list_records(
            limit=request.limit, status=TaskStatus.PENDING_ACTION
        )
        candidate_ids = [record.id for record in records]

    jobs = ScrapeJobService()
    queued: list[str] = []
    skipped: list[dict[str, str]] = []
    for record_id in candidate_ids:
        record = await history_service.get_record(record_id)
        if record is None:
            skipped.append({"id": record_id, "reason": "记录不存在"})
            continue
        if record.status != TaskStatus.PENDING_ACTION or record.conflict_type != ConflictType.NO_MATCH:
            skipped.append({"id": record.id, "reason": "仅支持待处理的 no_match 记录"})
            continue
        if not record.scrape_job_id:
            skipped.append({"id": record.id, "reason": "缺少原始任务"})
            continue
        old_job = await jobs.get_job(record.scrape_job_id)
        if old_job is None:
            skipped.append({"id": record.id, "reason": "原始任务不存在"})
            continue
        if old_job.file_locator is None and not Path(old_job.file_path).is_file():
            skipped.append({"id": record.id, "reason": "源文件不存在"})
            continue
        replacement = await jobs.create_replacement_job(old_job)
        if replacement is None:
            skipped.append({"id": record.id, "reason": "创建替代任务失败"})
            continue
        conflict_data = dict(record.conflict_data or {})
        conflict_data["replaced_by_job_id"] = replacement.id
        await history_service.update_record(
            record.id,
            status=TaskStatus.REPLACED,
            error_message="已创建 AI 重试替代任务",
            conflict_data=conflict_data,
        )
        queued.append(replacement.id)

    return {"queued_job_ids": queued, "skipped": skipped}


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
    """Move a history record to the deleted status."""
    deleted = await history_service.delete_record(record_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Record not found")
    return {"success": True, "message": "记录已移入已删除"}


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
    # FILE_CONFLICT: 放弃原匹配结果，使用用户重新选择的剧集/季/集
    resolution_action: str | None = None  # "rematch"


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
    from server.models.scraper import ScrapeStatus

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
    if result.status == ScrapeStatus.FILE_CONFLICT:
        conflict_data = dict(record.conflict_data or {}) if record else {}
        conflict_data.update({
            "output_dir": scrape_request.output_dir,
            "metadata_dir": scrape_request.metadata_dir,
            "link_mode": scrape_request.link_mode.value if scrape_request.link_mode else None,
            "tmdb_id": scrape_request.tmdb_id,
            "season": scrape_request.season,
            "episode": scrape_request.episode,
            "dest_path": result.dest_path,
        })
        await history_service.update_record(
            record_id,
            status=TaskStatus.PENDING_ACTION,
            error_message=result.message or "目标文件已存在",
            conflict_type=ConflictType.FILE_CONFLICT,
            conflict_data=conflict_data,
        )
        return {
            "success": False,
            "requires_action": True,
            "message": result.message or "目标文件已存在，请选择处理方式",
            "dest_path": result.dest_path,
        }
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

    # Skipped and deleted conflicts retain their conflict details and can be
    # reopened without rescanning the file or losing its original cloud locator.
    if record.status not in (
        TaskStatus.PENDING_ACTION,
        TaskStatus.SKIPPED,
        TaskStatus.DELETED,
    ):
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
            file_action=request.file_action,
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
            file_action=request.file_action,
            **locators,
        )
        return await _execute_scrape_and_update(history_service, record_id, scrape_request, user_log)

    elif request.conflict_type == ConflictType.FILE_CONFLICT:
        if request.resolution_action == "rematch":
            if request.tmdb_id is None:
                raise HTTPException(status_code=400, detail="请选择 TMDB ID")
            if request.season is None or request.episode is None:
                raise HTTPException(status_code=400, detail="请提供季/集号")

            user_log = (
                f"用户重新匹配: TMDB ID {request.tmdb_id}, "
                f"S{request.season:02d}E{request.episode:02d}"
            )
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
            return await _execute_scrape_and_update(
                history_service, record_id, scrape_request, user_log
            )

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
            file_action=request.file_action,
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


class SuccessRematchRequest(BaseModel):
    """Queue a safe, explicit correction for a successful history record."""

    tmdb_id: int
    season: int
    episode: int


def _get_success_output_path(record: HistoryRecord) -> Path | None:
    """Return the current local output recorded by a successful worker job."""
    marker = " => "
    if marker not in record.folder_path:
        return None
    _, output_path = record.folder_path.rsplit(marker, 1)
    output_path = output_path.strip()
    return Path(output_path) if output_path else None


@router.post("/{record_id}/rematch")
async def rematch_successful_record(
    record_id: str,
    request: SuccessRematchRequest,
    history_service: HistoryService = Depends(get_history_service),
) -> dict:
    """Queue a correction without mutating the original successful record.

    The worker copies a seven-day rollback backup before it changes media
    files.  Only a successful replacement marks the old record as ``replaced``.
    """
    from server.models.scrape_job import ScrapeJobCreate, ScrapeJobSource
    from server.services.scrape_job_service import ScrapeJobService

    record = await history_service.get_record(record_id)
    if record is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    if record.status != TaskStatus.SUCCESS:
        raise HTTPException(status_code=400, detail="仅支持修改成功记录的匹配")
    if not record.scrape_job_id:
        raise HTTPException(status_code=409, detail="该成功记录缺少原始任务，无法安全纠正")

    current_output = _get_success_output_path(record)
    if current_output is None or not current_output.is_file():
        raise HTTPException(
            status_code=409,
            detail="当前已整理文件不存在或不是本地输出，无法创建可回退的纠正任务",
        )

    jobs = ScrapeJobService()
    old_job = await jobs.get_job(record.scrape_job_id)
    if old_job is None:
        raise HTTPException(status_code=409, detail="原始任务不存在，无法安全纠正")

    correction = await jobs.create_job(
        ScrapeJobCreate(
            # 以当前已整理文件为输入，而不是原始来源，避免重跑时留下旧错误文件。
            file_path=str(current_output),
            output_dir=old_job.output_dir,
            metadata_dir=old_job.metadata_dir,
            output_locator=old_job.output_locator,
            metadata_locator=old_job.metadata_locator,
            allow_local_output=old_job.allow_local_output,
            link_mode=old_job.link_mode,
            source=ScrapeJobSource.MANUAL,
            source_id=old_job.source_id,
            advanced_settings=old_job.advanced_settings,
            replaces_job_id=old_job.id,
            correction_history_id=record.id,
            correction_tmdb_id=request.tmdb_id,
            correction_season=request.season,
            correction_episode=request.episode,
        )
    )
    if correction is None:
        raise HTTPException(status_code=409, detail="当前文件已有待处理任务，请等待其完成后再修改匹配")

    return {
        "success": True,
        "job_id": correction.id,
        "message": "已创建纠正任务；新任务成功后才会替代原成功记录，并保留 7 天备份",
    }


@router.post("/{record_id}/retry")
async def retry_scrape(
    record_id: str,
    request: RetryRequest,
    history_service: HistoryService = Depends(get_history_service),
) -> dict:
    """重试失败的刮削记录

    允许对 failed/timeout/cancelled/skipped/deleted 状态的记录重新执行刮削。
    """
    from server.models.scraper import ScrapeByIdRequest
    from server.models.organize import OrganizeMode

    # 1. 获取并验证记录
    record = await history_service.get_record(record_id)
    if record is None:
        raise HTTPException(status_code=404, detail="记录不存在")

    # 2. 验证状态允许重试
    retryable_statuses = [
        TaskStatus.FAILED,
        TaskStatus.TIMEOUT,
        TaskStatus.CANCELLED,
        TaskStatus.SKIPPED,
        TaskStatus.DELETED,
    ]
    if record.status not in retryable_statuses:
        raise HTTPException(
            status_code=400,
            detail=f"该记录状态为 {record.status.value}，不支持重试。仅支持 failed/timeout/cancelled/skipped/deleted 状态",
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
