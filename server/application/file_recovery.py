"""Reconcile durable file receipts before interrupted work can be queued again."""

import asyncio
import logging
from datetime import datetime
from pathlib import Path

from server.application.file_io import run_file_io
from server.common.path_security import validate_media_file_entry
from server.infrastructure import file_journal as journal
from server.models.history import HistoryConflictType, TaskStatus
from server.models.scrape_job import ScrapeJobStatus

logger = logging.getLogger(__name__)


def _finish_move(record):
    """Publication is committed; remove only the original entry we recorded."""
    if record['mode'] != 'move' or record['source'] == record['destination']:
        return
    source = Path(record['source'])
    if not source.exists() and not source.is_symlink():
        return
    if journal.signature(source) != record['source_signature']:
        raise journal.RecoveryConflict(f'移动源文件已被其他任务修改: {source}')
    validate_media_file_entry(str(source)).unlink()


def _consume_stage(record, outputs):
    staged = Path(record['destination'])
    if staged.exists() or staged.is_symlink():
        if not journal.owns_destination(record):
            raise journal.RecoveryConflict(f'让位文件已被替换: {staged}')
        # A symlink output may still depend on the staged entry. Restore its
        # original name and retargeting is unsafe, so retain its journal too.
        if any(Path(item['destination']).is_symlink() and Path(item['destination']).resolve() == staged.resolve() for item in outputs):
            raise journal.RecoveryConflict(f'软链接产物仍依赖让位文件，请保留: {staged}')
        staged.unlink()
    journal.finish_record(record)


async def _record_cleanup_result(service, job_id, error):
    try:
        await service.record_cleanup_result(job_id, error)
    except Exception:
        # Accounting cannot change a business result, especially after the
        # last receipt has already been removed by successful cleanup.
        logger.exception('无法保存文件清理结果，保留业务状态: %s', job_id)


def _superseded(job, record):
    return (job.status == ScrapeJobStatus.REPLACED or bool(job.replaced_by_job_id)
            or (record is not None and record.scrape_job_id != job.id))


async def _cleanup_superseded(service, job_id, media, stages, plans):
    """Retire private receipts without replaying writes to a successor's files."""
    error = None
    try:
        for plan in plans:
            await run_file_io(plan.cleanup)
        for item in media:
            await run_file_io(journal.finish_record, item)
        for stage in stages:
            path = Path(stage['destination'])
            if path.exists() or path.is_symlink():
                # A successor's symlink may still use this entry. Never remove
                # it or restore its original name using an obsolete receipt.
                raise journal.RecoveryConflict(f'旧任务让位文件需人工核对: {path}')
            await run_file_io(journal.finish_record, stage)
    except Exception as exc:
        error = str(exc) or type(exc).__name__
        logger.warning('已替代任务仅清理私有暂存，保留无法确认的内容 %s: %s', job_id, error)
    await _record_cleanup_result(service, job_id, error)


async def _reconcile_job(service, job_id, records):
    from server.application.history_service import HistoryService, build_record_folder_path
    from server.application.scraped_file_service import ScrapedFileService

    job = await service.get_job(job_id)
    if job is None:
        logger.warning('恢复记录对应的任务不存在，保留文件: %s', job_id)
        return
    history = HistoryService(db_path=service.db_path)
    history_id = job.history_record_id or job.continuation_history_id
    record = await history.get_record(history_id) if history_id else None
    media = [item for item in records if item['kind'] == 'media']
    stages = [item for item in records if item['kind'] == 'stage']
    plans = [journal.MetadataPlan(item) for item in records if item['kind'] == 'metadata']
    if _superseded(job, record):
        await _cleanup_superseded(service, job_id, media, stages, plans)
        return
    outputs = []
    try:
        for item in media:
            if await run_file_io(journal.owns_destination, item):
                outputs.append(item)
            elif item['phase'] in {'published', 'complete'}:
                raise journal.RecoveryConflict(f"已发布产物身份发生变化: {item['destination']}")
            elif item['phase'] in {'copying', 'prepared'}:
                available = journal.signature(Path(item['source'])) == item['source_signature']
                if not available:
                    # A failed retry may already have restored its staged input.
                    available = any(stage['destination'] == item['source'] and
                                    journal.signature(Path(stage['source'])) in
                                    (stage.get('source_signature'), stage.get('payload_signature'))
                                    for stage in stages)
                if not available:
                    raise journal.RecoveryConflict(f"发布未确认且源路径已变化，保留暂存内容: {item['source']}")
        correcting = job.operation == 'rematch'
        committed = bool(job.output_committed_at)
        rollback = correcting and not committed and job.status not in {ScrapeJobStatus.SUCCESS, ScrapeJobStatus.RUNNING, ScrapeJobStatus.PENDING}
        if outputs:
            output = outputs[-1]
            data = output.get('details') or {}
            if record is None:
                raise journal.RecoveryConflict('已发布产物缺少历史记录，保留恢复记录')
            # Persist the actual path before deleting any staging information.
            changed = await history.update_record(
                history_id, folder_path=build_record_folder_path(job.file_path, output['destination']),
                expected_job_id=job_id,
            )
            if not changed:
                await _cleanup_superseded(service, job_id, media, stages, plans)
                return
            await ScrapedFileService(db_path=service.db_path).register_output(
                history_record_id=history_id,
                source_path='' if job.continuation_history_id else job.file_path,
                target_path=output['destination'], tmdb_id=data.get('tmdb_id') or job.correction_tmdb_id or record.tmdb_id,
                title=data.get('title') or record.title,
                season=data.get('season', record.season_number), episode=data.get('episode', record.episode_number),
                replace_existing=True,
                expected_job_id=job_id,
            )
        for plan in plans:
            if any(item['destination'] == plan.record['destination'] for item in outputs) and not rollback:
                await run_file_io(plan.commit)
            else:
                await run_file_io(plan.rollback, cleanup=False)
        if outputs:
            if (job.status in {ScrapeJobStatus.RUNNING, ScrapeJobStatus.PENDING}
                    or (job.status == ScrapeJobStatus.SUCCESS and not committed)):
                # History success can precede the final job/correction commit.
                # Finish that interrupted transaction after verifying outputs.
                changed = await history.update_record(history_id, status=TaskStatus.SUCCESS, error_message='',
                                            tmdb_id=data.get('tmdb_id'), title=data.get('title'),
                                            season_number=data.get('season'), episode_number=data.get('episode'),
                                            expected_job_id=job_id)
                if not changed:
                    await _cleanup_superseded(service, job_id, media, stages, plans)
                    return
                if correcting:
                    await service._repo.complete_correction(job_id)
                else:
                    await service.update_job(job_id, status=ScrapeJobStatus.SUCCESS, finished_at=datetime.now(), error_message='')
                committed = True
        cleanup_error = None
        try:
            if outputs:
                for item in outputs:
                    await run_file_io(_finish_move, item)
                for stage in stages:
                    await run_file_io(_consume_stage, stage, outputs)
            else:
                for stage in stages:
                    await run_file_io(journal.restore_stage, stage)
            for plan in plans:
                await run_file_io(plan.cleanup)
            for item in media:
                await run_file_io(journal.finish_record, item)
        except journal.RecoveryConflict:
            raise  # An ownership conflict still needs explicit inspection.
        except OSError as exc:
            cleanup_error = str(exc) or type(exc).__name__
            logger.warning('文件清理未完成，保留业务状态与恢复记录 %s: %s', job_id, cleanup_error)
        await _record_cleanup_result(service, job_id, cleanup_error)
        if committed and cleanup_error is None and job.status == ScrapeJobStatus.PENDING_ACTION:
            # Repair legacy cleanup failures after ownership checks succeed.
            changed = await service.update_job(job_id, status=ScrapeJobStatus.SUCCESS,
                                               expected_status=ScrapeJobStatus.PENDING_ACTION, error_message='')
            current = await history.get_record(history_id) if changed and history_id else None
            if current and current.scrape_job_id == job_id and current.status == TaskStatus.PENDING_ACTION:
                await history.update_record(history_id, status=TaskStatus.SUCCESS, error_message='',
                                            expected_job_id=job_id)
    except Exception as exc:
        current_job = await service.get_job(job_id)
        current = await history.get_record(history_id) if history_id else None
        if current_job is None:
            return
        if _superseded(current_job, current):
            await _cleanup_superseded(service, job_id, media, stages, plans)
            return
        logger.exception('文件操作恢复需要人工处理: %s', job_id)
        message = f'文件操作恢复待处理，已保留文件与恢复记录: {exc}'
        changed = await service.update_job(job_id, status=ScrapeJobStatus.PENDING_ACTION,
                                 finished_at=datetime.now(), error_message=message,
                                 expected_status=current_job.status)
        if changed and current:
            await history.update_record(history_id, status=TaskStatus.PENDING_ACTION,
                                        expected_job_id=job_id,
                                        error_message=message, conflict_type=HistoryConflictType.FILE_CONFLICT,
                                        conflict_data={
                                            'tmdb_id': job.correction_tmdb_id or current.tmdb_id,
                                            'season': job.correction_season or current.season_number,
                                            'episode': job.correction_episode or current.episode_number,
                                            'output_dir': job.output_dir, 'metadata_dir': job.metadata_dir,
                                            'link_mode': job.link_mode.value if job.link_mode else None,
                                            'dest_path': outputs[-1]['destination'] if outputs else job.file_path,
                                            'recovery_paths': [item.get('destination') for item in records],
                                        })


async def reconcile_file_operations(service, job_id=None):
    records = await run_file_io(journal.read_records, job_id)
    grouped = {}
    for record in records:
        if record.get('owner'):
            grouped.setdefault(record['owner'], []).append(record)
    for owner, items in grouped.items():
        await _reconcile_job(service, owner, items)


async def drain_recovery(service, job_id, *, cancelled=False):
    """Repeated cancellation must not abandon a committed file's DB receipt."""
    async def settle():
        # A second cancellation can interrupt the body's terminal DB write.
        # Finish that transition in the same shielded task as file recovery.
        if cancelled:
            from server.application.history_service import HistoryService
            job = await service.get_job(job_id)
            if job and job.status in {ScrapeJobStatus.PENDING, ScrapeJobStatus.RUNNING, ScrapeJobStatus.PENDING_ACTION}:
                changed = await service.update_job(job_id, status=ScrapeJobStatus.CANCELLED,
                                                   expected_status=job.status, finished_at=datetime.now())
                if changed:
                    job = await service.get_job(job_id)
            if job and job.status == ScrapeJobStatus.CANCELLED:
                history_id = job.history_record_id or job.continuation_history_id
                if history_id:
                    await HistoryService(db_path=service.db_path).update_record(
                        history_id, status=TaskStatus.CANCELLED, expected_job_id=job_id)
        await reconcile_file_operations(service, job_id)

    task = asyncio.create_task(settle())
    interrupted = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            interrupted = True
    task.result()
    if interrupted:
        raise asyncio.CancelledError
