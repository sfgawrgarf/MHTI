"""History API endpoints."""

import asyncio
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from sse_starlette.sse import EventSourceResponse

from server.application.history_actions import HistoryScrapeActions
from server.application.history_files import HistoryFileService
from server.api.deps import require_auth
from server.api.deps import (
    get_config_service,
    get_history_service,
    get_manual_job_service,
    get_scraped_file_service,
    get_scrape_job_service,
)
from server.application.scraped_file_service import ScrapedFileService
from server.domain.system.config_service import ConfigService
from server.models.history import (
    HistoryConflictType,
    HistoryFileDeleteRequest,
    HistoryFileDeleteResponse,
    HistoryFileListResponse,
    HistoryRecord,
    HistoryRecordCreate,
    HistoryRecordDetail,
    HistoryListResponse,
    TaskStatus,
    UndoResponse,
)
from server.models.organize import OrganizeMode
from server.models.scraper import ScrapeByIdRequest
from server.models.scrape_job import ScrapeJobCreate, ScrapeJobSource
from server.models.storage import is_p115_to_local
from server.application.history_service import HistoryService
from server.application.manual_job_service import ManualJobService
from server.application.scrape_job_service import ScrapeJobService
from server.common.path_security import PathSecurityError, validate_media_path

router = APIRouter(prefix="/api/history", tags=["history"], dependencies=[Depends(require_auth)])


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


@router.post("/undo", response_model=UndoResponse)
async def undo_last(
    history_service: HistoryService = Depends(get_history_service),
) -> UndoResponse:
    """撤销最近一次可撤销操作（删除单条记录 / 清空记录）。

    物理删除的文件不在这里——磁盘上已经没有了，撤销无从谈起。
    放在 /{record_id} 之前声明，避免被详情路由吃掉。
    """
    return await history_service.undo_last()


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
    """Batch retry unresolved no-match records through the AI-enabled worker."""

    record_ids: list[str] | None = Field(default=None, max_length=500)
    limit: int = Field(default=100, ge=1, le=500)
    all_pending: bool = False


async def _restore_locators_from_scrape_job(record) -> dict:
    """Restore persisted job storage context for a manual history action."""
    if not getattr(record, "scrape_job_id", None):
        return {}
    try:
        from server.application import scrape_job_service as scrape_job_module

        service = scrape_job_module.ScrapeJobService()
        job = await service.get_job(record.scrape_job_id)
        if job is None:
            return {}
        allow_local_output = bool(getattr(job, "allow_local_output", False))
        if (
            not allow_local_output
            and getattr(job, "source", ScrapeJobSource.MANUAL) == ScrapeJobSource.WATCHER
            and is_p115_to_local(
                source_path=job.file_path,
                source_locator=getattr(job, "file_locator", None),
                target_path=job.output_dir,
                target_locator=getattr(job, "output_locator", None),
            )
        ):
            allow_local_output = True
        values = {
            "file_locator": getattr(job, "file_locator", None),
            "output_locator": getattr(job, "output_locator", None),
            "metadata_locator": getattr(job, "metadata_locator", None),
            "allow_local_output": allow_local_output,
            "output_dir": getattr(job, "output_dir", None),
            "metadata_dir": getattr(job, "metadata_dir", None),
            "link_mode": getattr(job, "link_mode", None),
            "advanced_settings": getattr(job, "advanced_settings", None),
        }
        return {
            key: value
            for key, value in values.items()
            if value is not None and value is not False
        }
    except Exception:
        return {}


async def _execute_scrape_and_update(
    history_service: HistoryService,
    record_id: str,
    scrape_request: ScrapeByIdRequest,
    user_selection_log: str | None = None,
) -> dict:
    """Queue a manual history action through the durable scrape-job worker."""
    from server.application import scrape_job_service as scrape_job_module

    record = await history_service.get_record(record_id)
    if record is None:
        raise HTTPException(status_code=404, detail="记录不存在")

    service = scrape_job_module.ScrapeJobService()
    job = await service.create_job(
        ScrapeJobCreate(
            file_path=scrape_request.file_path,
            output_dir=scrape_request.output_dir or str(Path(scrape_request.file_path).parent),
            metadata_dir=scrape_request.metadata_dir,
            file_locator=scrape_request.file_locator,
            output_locator=scrape_request.output_locator,
            metadata_locator=scrape_request.metadata_locator,
            allow_local_output=scrape_request.allow_local_output,
            link_mode=scrape_request.link_mode,
            advanced_settings=scrape_request.advanced_settings,
            source=ScrapeJobSource.MANUAL,
            source_id=getattr(record, "manual_job_id", None),
            replaces_job_id=getattr(record, "scrape_job_id", None),
            continuation_history_id=record_id,
            correction_tmdb_id=scrape_request.tmdb_id,
            correction_season=scrape_request.season,
            correction_episode=scrape_request.episode,
            file_action=scrape_request.file_action,
            skip_emby_check=scrape_request.skip_emby_check,
            selection_log=user_selection_log,
        ),
        skip_duplicate_check=True,
    )
    if job is None:
        raise HTTPException(status_code=409, detail="该记录或文件已在处理中，请勿重复提交")
    return {
        "success": True,
        "queued": True,
        "job_id": job.id,
        "message": "已加入刮削队列，请在记录页查看结果",
    }


async def _list_all_pending_record_ids(history_service: HistoryService) -> list[str]:
    """Collect all pending-action IDs without inheriting UI pagination."""
    batch_size = 500
    offset = 0
    record_ids: list[str] = []
    while True:
        records, total = await history_service.list_records(
            limit=batch_size,
            offset=offset,
            status=TaskStatus.PENDING_ACTION,
        )
        record_ids.extend(record.id for record in records)
        offset += len(records)
        if not records or offset >= total:
            break
    return record_ids


@router.post("/ai-retry")
async def retry_no_match_with_ai(
    request: AIRetryRequest,
    history_service: HistoryService = Depends(get_history_service),
) -> dict:
    """Queue fresh replacement jobs; do not overwrite the old history record."""
    if request.all_pending and request.record_ids:
        raise HTTPException(
            status_code=400,
            detail="all_pending 与 record_ids 不能同时使用",
        )
    if request.all_pending:
        candidate_ids = (await _list_all_pending_record_ids(history_service))[: request.limit]
    elif request.record_ids:
        candidate_ids = request.record_ids[: request.limit]
    else:
        records, _ = await history_service.list_records(
            limit=request.limit,
            status=TaskStatus.PENDING_ACTION,
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
        if record.status != TaskStatus.PENDING_ACTION or record.conflict_type != HistoryConflictType.NO_MATCH:
            skipped.append({"id": record.id, "reason": "仅支持待处理的 no_match 记录"})
            continue
        if not record.scrape_job_id:
            skipped.append({"id": record.id, "reason": "缺少原始任务"})
            continue
        old_job = await jobs.get_job(record.scrape_job_id)
        if old_job is None:
            skipped.append({"id": record.id, "reason": "原始任务不存在"})
            continue
        if old_job.file_locator is None:
            try:
                validate_media_path(old_job.file_path, must_exist=True, require_file=True)
            except PathSecurityError:
                skipped.append({"id": record.id, "reason": "源文件不存在或不在允许目录"})
                continue
        replacement = await jobs.create_replacement_job(
            old_job,
            replacement_history_id=record.id,
        )
        if replacement is None:
            skipped.append({"id": record.id, "reason": "记录已被处理或文件已有运行中任务"})
            continue
        queued.append(replacement.id)

    return {"queued_job_ids": queued, "skipped": skipped}


@router.delete("")
async def clear_records(
    before_days: int | None = Query(None, ge=1, description="Clear records older than N days"),
    history_service: HistoryService = Depends(get_history_service),
) -> dict:
    """Clear history records."""
    try:
        deleted = await history_service.clear_records(before_days=before_days)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
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
    try:
        deleted = await history_service.delete_record(record_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="Record not found")
    return {"success": True, "message": "记录已删除"}


class ResolveConflictRequest(BaseModel):
    """请求模型：处理冲突"""

    conflict_type: HistoryConflictType
    # NEED_SELECTION: 选择的 TMDB ID
    tmdb_id: int | None = None
    # NEED_SEASON_EPISODE: 季/集号
    season: int | None = None
    episode: int | None = None
    # FILE_CONFLICT: 处理方式
    file_action: Literal["overwrite", "skip", "rename", "force"] | None = None
    resolution_action: Literal["rematch"] | None = None


def get_history_file_service(
    history_service: HistoryService = Depends(get_history_service),
    scraped_file_service: ScrapedFileService = Depends(get_scraped_file_service),
    config_service: ConfigService = Depends(get_config_service),
    scrape_job_service: ScrapeJobService = Depends(get_scrape_job_service),
    manual_job_service: ManualJobService = Depends(get_manual_job_service),
) -> HistoryFileService:
    """构造记录文件用例（依赖经 FastAPI 覆盖链注入）。"""
    return HistoryFileService(
        history_service,
        scraped_file_service,
        config_service,
        scrape_job_service,
        manual_job_service,
    )


def get_history_scrape_actions(
    history_service: HistoryService = Depends(get_history_service),
    scrape_job_service: ScrapeJobService = Depends(get_scrape_job_service),
    scraped_file_service: ScrapedFileService = Depends(get_scraped_file_service),
    files: HistoryFileService = Depends(get_history_file_service),
) -> HistoryScrapeActions:
    """构造历史刮削用例（依赖经 FastAPI 覆盖链注入）。"""
    return HistoryScrapeActions(history_service, scrape_job_service, scraped_file_service, files)


def _build_scrape_request(
    record: HistoryRecordDetail,
    *,
    tmdb_id: int,
    season: int,
    episode: int,
    output_dir: str | None,
    metadata_dir: str | None,
    link_mode: OrganizeMode | None,
    locators: dict | None = None,
    skip_emby_check: bool = False,
    file_action: str | None = None,
) -> ScrapeByIdRequest:
    """构建刮削请求（重试与各冲突分支共用）。"""
    kwargs: dict = {
        "file_path": record.folder_path,
        "tmdb_id": tmdb_id,
        "season": season,
        "episode": episode,
        "output_dir": output_dir,
        "metadata_dir": metadata_dir,
        "link_mode": link_mode,
    }
    if skip_emby_check:
        kwargs["skip_emby_check"] = True
    if file_action in ("overwrite", "rename"):
        kwargs["file_action"] = file_action
    if locators:
        kwargs.update(locators)
    return ScrapeByIdRequest(**kwargs)


async def _resolve_conflict_compat(
    record_id: str,
    request: ResolveConflictRequest,
    history_service: HistoryService,
) -> dict:
    """Compatibility path for direct callers of the pre-use-case route API."""
    record = await history_service.get_record(record_id)
    if record is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    if record.status not in (
        TaskStatus.PENDING_ACTION,
        TaskStatus.SKIPPED,
        TaskStatus.DELETED,
    ):
        raise HTTPException(status_code=400, detail="该记录不需要处理")
    if record.conflict_type != request.conflict_type:
        raise HTTPException(status_code=400, detail="冲突类型不匹配")

    if request.file_action == "skip" and request.resolution_action != "rematch":
        await history_service.update_record(
            record_id, status=TaskStatus.SKIPPED, error_message="用户跳过"
        )
        return {"success": True, "message": "已跳过"}

    conflict_data = record.conflict_data or {}
    locators = await _restore_locators_from_scrape_job(record)
    output_dir = locators.pop("output_dir", conflict_data.get("output_dir"))
    metadata_dir = locators.pop("metadata_dir", conflict_data.get("metadata_dir"))
    link_mode = locators.pop("link_mode", None)
    if link_mode is None and conflict_data.get("link_mode"):
        link_mode = OrganizeMode(conflict_data["link_mode"])

    if request.resolution_action == "rematch":
        if request.tmdb_id is None:
            raise HTTPException(status_code=400, detail="请选择 TMDB ID")
        if request.season is None or request.episode is None:
            raise HTTPException(status_code=400, detail="请提供季/集号")
        return await _execute_scrape_and_update(
            history_service,
            record_id,
            ScrapeByIdRequest(
                file_path=record.folder_path,
                tmdb_id=request.tmdb_id,
                season=request.season,
                episode=request.episode,
                output_dir=output_dir,
                metadata_dir=metadata_dir,
                link_mode=link_mode,
                file_action=request.file_action,
                **locators,
            ),
            f"用户重新匹配: TMDB ID {request.tmdb_id}, S{request.season:02d}E{request.episode:02d}",
        )

    if request.conflict_type == HistoryConflictType.NEED_SELECTION:
        if request.tmdb_id is None:
            raise HTTPException(status_code=400, detail="请选择 TMDB ID")
        if request.season is None or request.episode is None:
            raise HTTPException(status_code=400, detail="请提供季/集号")
        selected_name = f"TMDB ID: {request.tmdb_id}"
        for result in conflict_data.get("search_results", []):
            if result.get("id") == request.tmdb_id:
                selected_name = result.get("name", selected_name)
                break
        message = f"用户选择了「{selected_name}」S{request.season:02d}E{request.episode:02d}"
        return await _execute_scrape_and_update(
            history_service,
            record_id,
            _build_scrape_request(
                record,
                tmdb_id=request.tmdb_id,
                season=request.season,
                episode=request.episode,
                output_dir=output_dir,
                metadata_dir=metadata_dir,
                link_mode=link_mode,
                locators=locators,
                file_action=request.file_action,
            ),
            message,
        )

    if request.conflict_type == HistoryConflictType.NEED_SEASON_EPISODE:
        if request.season is None or request.episode is None:
            raise HTTPException(status_code=400, detail="请提供季/集号")
        tmdb_id = conflict_data.get("tmdb_id")
        if tmdb_id is None:
            raise HTTPException(status_code=400, detail="缺少 TMDB ID")
        return await _execute_scrape_and_update(
            history_service,
            record_id,
            _build_scrape_request(
                record,
                tmdb_id=tmdb_id,
                season=request.season,
                episode=request.episode,
                output_dir=output_dir,
                metadata_dir=metadata_dir,
                link_mode=link_mode,
                locators=locators,
                file_action=request.file_action,
            ),
            f"用户选择了 S{request.season:02d}E{request.episode:02d}",
        )

    if request.conflict_type == HistoryConflictType.FILE_CONFLICT:
        if request.file_action not in ("overwrite", "rename"):
            raise HTTPException(status_code=400, detail="无效的处理方式")
        tmdb_id = conflict_data.get("tmdb_id")
        if tmdb_id is None:
            raise HTTPException(status_code=400, detail="缺少 TMDB ID")
        action_text = "覆盖" if request.file_action == "overwrite" else "重命名"
        return await _execute_scrape_and_update(
            history_service,
            record_id,
            _build_scrape_request(
                record,
                tmdb_id=tmdb_id,
                season=conflict_data.get("season", 1),
                episode=conflict_data.get("episode", 1),
                output_dir=output_dir,
                metadata_dir=metadata_dir,
                link_mode=link_mode,
                locators=locators,
                file_action=request.file_action,
            ),
            f"用户选择了{action_text}文件",
        )

    if request.conflict_type in (
        HistoryConflictType.NO_MATCH,
        HistoryConflictType.SEARCH_FAILED,
        HistoryConflictType.API_FAILED,
    ):
        if request.tmdb_id is None:
            raise HTTPException(status_code=400, detail="请输入 TMDB ID")
        if request.season is None or request.episode is None:
            raise HTTPException(status_code=400, detail="请提供季/集号")
        return await _execute_scrape_and_update(
            history_service,
            record_id,
            _build_scrape_request(
                record,
                tmdb_id=request.tmdb_id,
                season=request.season,
                episode=request.episode,
                output_dir=output_dir,
                metadata_dir=metadata_dir,
                link_mode=link_mode,
                locators=locators,
            ),
            f"用户手动输入 TMDB ID: {request.tmdb_id}, S{request.season:02d}E{request.episode:02d}",
        )

    if request.conflict_type == HistoryConflictType.EMBY_CONFLICT:
        tmdb_id = conflict_data.get("tmdb_id")
        if tmdb_id is None:
            raise HTTPException(status_code=400, detail="缺少 TMDB ID")
        season = request.season if request.season is not None else conflict_data.get("season", 1)
        episode = request.episode if request.episode is not None else conflict_data.get("episode", 1)
        return await _execute_scrape_and_update(
            history_service,
            record_id,
            _build_scrape_request(
                record,
                tmdb_id=tmdb_id,
                season=season,
                episode=episode,
                output_dir=output_dir,
                metadata_dir=metadata_dir,
                link_mode=link_mode,
                locators=locators,
                skip_emby_check=True,
            ),
            (
                f"用户强制继续刮削 S{season:02d}E{episode:02d}（忽略 Emby 冲突）"
                if request.file_action == "force"
                else f"用户选择刮削为 S{season:02d}E{episode:02d}"
            ),
        )

    raise HTTPException(status_code=400, detail="未知的冲突类型")


@router.put("/{record_id}/resolve")
async def resolve_conflict(
    record_id: str,
    request: ResolveConflictRequest,
    history_service: HistoryService = Depends(get_history_service),
    actions: HistoryScrapeActions = Depends(get_history_scrape_actions),
) -> dict:
    """处理待处理的冲突记录"""
    # 保留旧路由函数的直接调用兼容性；生产请求由 FastAPI 注入新的用例对象。
    if not isinstance(actions, HistoryScrapeActions):
        return await _resolve_conflict_compat(record_id, request, history_service)

    # 获取记录
    record = await history_service.get_record(record_id)
    if record is None:
        raise HTTPException(status_code=404, detail="记录不存在")

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
    link_mode = OrganizeMode(link_mode_value) if link_mode_value else None

    # 恢复 locator（支持 115 等云端文件重试）
    locators = await actions.restore_locators(record)

    if request.resolution_action == "rematch":
        if request.tmdb_id is None:
            raise HTTPException(status_code=400, detail="请选择 TMDB ID")
        if request.season is None or request.episode is None:
            raise HTTPException(status_code=400, detail="请提供季/集号")
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
        return await actions.queue_scrape_and_update(
            record_id,
            scrape_request,
            f"用户重新匹配: TMDB ID {request.tmdb_id}, "
            f"S{request.season:02d}E{request.episode:02d}",
        )

    # 根据冲突类型处理
    if request.conflict_type == HistoryConflictType.NEED_SELECTION:
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

        scrape_request = _build_scrape_request(
            record,
            tmdb_id=request.tmdb_id,
            season=request.season,
            episode=request.episode,
            output_dir=output_dir,
            metadata_dir=metadata_dir,
            link_mode=link_mode,
            locators=locators,
            file_action=request.file_action,
        )
        return await actions.queue_scrape_and_update(record_id, scrape_request, user_log)

    elif request.conflict_type == HistoryConflictType.NEED_SEASON_EPISODE:
        if request.season is None or request.episode is None:
            raise HTTPException(status_code=400, detail="请提供季/集号")

        tmdb_id = record.conflict_data.get("tmdb_id") if record.conflict_data else None
        if tmdb_id is None:
            raise HTTPException(status_code=400, detail="缺少 TMDB ID")

        user_log = f"用户选择了 S{request.season:02d}E{request.episode:02d}"

        scrape_request = _build_scrape_request(
            record,
            tmdb_id=tmdb_id,
            season=request.season,
            episode=request.episode,
            output_dir=output_dir,
            metadata_dir=metadata_dir,
            link_mode=link_mode,
            locators=locators,
            file_action=request.file_action,
        )
        return await actions.queue_scrape_and_update(record_id, scrape_request, user_log)

    elif request.conflict_type == HistoryConflictType.FILE_CONFLICT:
        if request.file_action not in ("overwrite", "skip", "rename"):
            raise HTTPException(status_code=400, detail="无效的处理方式")

        if request.file_action == "skip":
            return await actions.skip_record(record_id)

        tmdb_id = record.conflict_data.get("tmdb_id") if record.conflict_data else None
        if tmdb_id is None:
            raise HTTPException(status_code=400, detail="缺少 TMDB ID")

        action_text = "覆盖" if request.file_action == "overwrite" else "重命名"
        user_log = f"用户选择了{action_text}文件"

        scrape_request = _build_scrape_request(
            record,
            tmdb_id=tmdb_id,
            season=record.conflict_data.get("season", 1) if record.conflict_data else 1,
            episode=record.conflict_data.get("episode", 1) if record.conflict_data else 1,
            output_dir=output_dir,
            metadata_dir=metadata_dir,
            link_mode=link_mode,
            locators=locators,
            file_action=request.file_action,
        )
        return await actions.queue_scrape_and_update(record_id, scrape_request, user_log)

    elif request.conflict_type in (HistoryConflictType.NO_MATCH, HistoryConflictType.SEARCH_FAILED, HistoryConflictType.API_FAILED):
        # 手动输入 TMDB ID 的情况
        if request.tmdb_id is None:
            raise HTTPException(status_code=400, detail="请输入 TMDB ID")
        if request.season is None or request.episode is None:
            raise HTTPException(status_code=400, detail="请提供季/集号")

        user_log = f"用户手动输入 TMDB ID: {request.tmdb_id}, S{request.season:02d}E{request.episode:02d}"

        scrape_request = _build_scrape_request(
            record,
            tmdb_id=request.tmdb_id,
            season=request.season,
            episode=request.episode,
            output_dir=output_dir,
            metadata_dir=metadata_dir,
            link_mode=link_mode,
            locators=locators,
            file_action=request.file_action,
        )
        return await actions.queue_scrape_and_update(record_id, scrape_request, user_log)

    elif request.conflict_type == HistoryConflictType.EMBY_CONFLICT:
        # Emby 冲突处理
        if request.file_action == "skip":
            return await actions.skip_record(record_id, "用户跳过（Emby 已存在）")

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

        scrape_request = _build_scrape_request(
            record,
            tmdb_id=tmdb_id,
            season=season,
            episode=episode,
            output_dir=output_dir,
            metadata_dir=metadata_dir,
            link_mode=link_mode,
            locators=None,
            skip_emby_check=True,  # 跳过 Emby 检查
        )
        return await actions.queue_scrape_and_update(record_id, scrape_request, user_log)

    raise HTTPException(status_code=400, detail="未知的冲突类型")


class RetryRequest(BaseModel):
    """请求模型：重试刮削"""

    tmdb_id: int  # TMDB ID
    season: int  # 季号
    episode: int  # 集号


class SuccessRematchRequest(BaseModel):
    """为成功记录创建显式的纠正任务，不直接改写原记录。"""

    tmdb_id: int
    season: int
    episode: int


def _get_success_output_path(record) -> Path | None:
    """从成功记录中取出当前本地产物路径。"""
    marker = " => "
    folder_path = getattr(record, "folder_path", "")
    if marker not in folder_path:
        return None
    _, output_path = folder_path.rsplit(marker, 1)
    output_path = output_path.strip()
    return Path(output_path) if output_path else None


@router.post("/{record_id}/rematch")
async def rematch_successful_record(
    record_id: str,
    request: SuccessRematchRequest,
    history_service: HistoryService = Depends(get_history_service),
) -> dict:
    """为成功记录排队纠正任务，只有新任务成功后才替代原记录。"""
    record = await history_service.get_record(record_id)
    if record is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    if record.status != TaskStatus.SUCCESS:
        raise HTTPException(status_code=400, detail="仅支持修改成功记录的匹配")
    if not getattr(record, "scrape_job_id", None):
        raise HTTPException(status_code=409, detail="该成功记录缺少原始任务，无法安全纠正")

    current_output = _get_success_output_path(record)
    if current_output is None or not current_output.is_file():
        raise HTTPException(
            status_code=409,
            detail="当前已整理文件不存在或不是本地输出，无法创建可回退的纠正任务",
        )

    from server.application import scrape_job_service as scrape_job_module

    jobs = scrape_job_module.ScrapeJobService()
    old_job = await jobs.get_job(record.scrape_job_id)
    if old_job is None:
        raise HTTPException(status_code=409, detail="原始任务不存在，无法安全纠正")

    correction = await jobs.create_job(
        ScrapeJobCreate(
            file_path=str(current_output),
            output_dir=old_job.output_dir,
            metadata_dir=getattr(old_job, "metadata_dir", None),
            output_locator=getattr(old_job, "output_locator", None),
            metadata_locator=getattr(old_job, "metadata_locator", None),
            allow_local_output=getattr(old_job, "allow_local_output", False),
            link_mode=getattr(old_job, "link_mode", None),
            source=ScrapeJobSource.MANUAL,
            source_id=getattr(old_job, "source_id", None),
            advanced_settings=getattr(old_job, "advanced_settings", None),
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
    actions: HistoryScrapeActions = Depends(get_history_scrape_actions),
) -> dict:
    """重刮记录（可按指定 TMDB ID / 季 / 集）。

    状态限制只保留「正在处理中」：这条接口同时承担「内联 TMDB ID 直接重刮」，
    用于修正匹配错的成功记录——只允许 failed/timeout/cancelled 时无法修正错误匹配。
    已经跑完的记录允许重刮，代价是可能产生重复产物（旧产物由后端先清掉），
    所以前端必须二次确认。

    输入文件不由调用方指定：后端按「源文件优先、源文件不存在就用整理后的产物」
    自行解析（resolve_input），否则拿 folder_path 那串「源 => 产物」当路径会直接报文件不存在。
    """
    # 直接调用旧函数签名时，使用兼容路径；生产请求走持久化用例。
    if not isinstance(actions, HistoryScrapeActions):
        record = await history_service.get_record(record_id)
        if record is None:
            raise HTTPException(status_code=404, detail="记录不存在")
        retryable_statuses = {
            TaskStatus.FAILED,
            TaskStatus.TIMEOUT,
            TaskStatus.CANCELLED,
            TaskStatus.SKIPPED,
            TaskStatus.DELETED,
        }
        if record.status not in retryable_statuses:
            raise HTTPException(
                status_code=400,
                detail=f"该记录状态为 {record.status.value}，不支持重试",
            )
        conflict_data = record.conflict_data or {}
        locators = await _restore_locators_from_scrape_job(record)
        output_dir = locators.pop("output_dir", conflict_data.get("output_dir"))
        metadata_dir = locators.pop("metadata_dir", conflict_data.get("metadata_dir"))
        link_mode = locators.pop("link_mode", None)
        if link_mode is None and conflict_data.get("link_mode"):
            link_mode = OrganizeMode(conflict_data["link_mode"])
        scrape_request = _build_scrape_request(
            record,
            tmdb_id=request.tmdb_id,
            season=request.season,
            episode=request.episode,
            output_dir=output_dir,
            metadata_dir=metadata_dir,
            link_mode=link_mode,
            locators=locators,
        )
        return await _execute_scrape_and_update(
            history_service,
            record_id,
            scrape_request,
            f"用户手动重试: TMDB ID {request.tmdb_id}, S{request.season:02d}E{request.episode:02d}",
        )

    # 1. 获取并验证记录
    record = await history_service.get_record(record_id)
    if record is None:
        raise HTTPException(status_code=404, detail="记录不存在")

    # 2. 只有明确的失败/终止状态允许重试；成功记录走独立纠正接口。
    retryable_statuses = {
        TaskStatus.FAILED,
        TaskStatus.TIMEOUT,
        TaskStatus.CANCELLED,
        TaskStatus.SKIPPED,
        TaskStatus.DELETED,
    }
    if record.status not in retryable_statuses:
        raise HTTPException(
            status_code=400,
            detail=f"该记录状态为 {record.status.value}，不支持重试",
        )

    # 3. 从 conflict_data 恢复原始参数
    conflict_data = record.conflict_data or {}
    output_dir = conflict_data.get("output_dir")
    metadata_dir = conflict_data.get("metadata_dir")
    link_mode_value = conflict_data.get("link_mode")
    link_mode = OrganizeMode(link_mode_value) if link_mode_value else None

    # 4. 构建刮削请求（恢复 locator 以支持 115 等云端文件）
    user_log = f"用户手动重试: TMDB ID {request.tmdb_id}, S{request.season:02d}E{request.episode:02d}"
    locators = await actions.restore_locators(record)
    output_dir = locators.pop("output_dir", output_dir)
    metadata_dir = locators.pop("metadata_dir", metadata_dir)
    link_mode = locators.pop("link_mode", link_mode)

    scrape_request = _build_scrape_request(
        record,
        tmdb_id=request.tmdb_id,
        season=request.season,
        episode=request.episode,
        output_dir=output_dir,
        metadata_dir=metadata_dir,
        link_mode=link_mode,
        locators=locators,
    )

    # 5. 执行刮削（输入解析与旧产物清理在用例层完成）
    return await actions.execute_scrape_and_update(
        record_id, scrape_request, user_log, resolve_input=True
    )


@router.get("/{record_id}/files", response_model=HistoryFileListResponse)
async def list_record_files(
    record_id: str,
    files: HistoryFileService = Depends(get_history_file_service),
) -> HistoryFileListResponse:
    """记录关联的源文件与刮削产物清单（删除确认弹窗据此列出具体路径）。"""
    try:
        return await files.list_files(record_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="记录不存在")


@router.post("/{record_id}/files/delete", response_model=HistoryFileDeleteResponse)
async def delete_record_files(
    record_id: str,
    request: HistoryFileDeleteRequest,
    files: HistoryFileService = Depends(get_history_file_service),
) -> HistoryFileDeleteResponse:
    """物理删除记录的源文件 / 刮削产物（不可恢复，前端必须二次确认）。"""
    try:
        return await files.delete_files(record_id, request.scope)
    except LookupError:
        raise HTTPException(status_code=404, detail="记录不存在")


@router.post("/{record_id}/reorganize")
async def reorganize_record(
    record_id: str,
    actions: HistoryScrapeActions = Depends(get_history_scrape_actions),
) -> dict:
    """用已存元数据重跑整理（不请求 TMDB）。"""
    return await actions.reorganize_from_metadata(record_id)
