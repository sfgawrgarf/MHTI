"""Durable seven-day snapshots for non-destructive successful-record correction."""

import json
import logging
import shutil
import time
from pathlib import Path

from server.application.file_io import run_file_io, check_file_cancelled
from server.application.history_files import HistoryFileService
from server.application.history_service import HistoryService
from server.application.scraped_file_service import ScrapedFileService
from server.common.path_security import validate_media_path
from server.domain.system.config_service import ConfigService
from server.infrastructure.config import DATA_DIR
from server.infrastructure.file_operations import publish_file
from server.infrastructure.file_journal import operation_kind
from server.models.history import HistoryFileRole
from server.models.organize import OrganizeMode

logger = logging.getLogger(__name__)
BACKUP_ROOT = DATA_DIR / "correction-backups"
RETENTION_SECONDS = 7 * 24 * 3600


def _save_snapshot(job_id: str, history_id: str, paths: list[str]) -> Path:
    if not job_id or any(char not in "0123456789abcdef-" for char in job_id):
        raise ValueError("无效的备份任务 ID")
    BACKUP_ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    if BACKUP_ROOT.is_symlink() or not BACKUP_ROOT.resolve().is_relative_to(DATA_DIR.resolve()):
        raise ValueError("纠正备份目录不能是外部软链接")
    directory = BACKUP_ROOT / job_id
    directory.mkdir(mode=0o700, exist_ok=True)
    if directory.is_symlink():
        raise ValueError("纠正备份目录不能是软链接")
    manifest_path = directory / "manifest.json"
    if manifest_path.exists():
        previous = json.loads(manifest_path.read_text())
        if previous.get("complete"):
            return directory
    manifest = {
        "history_id": history_id, "job_id": job_id,
        "expires_at": time.time() + RETENTION_SECONDS,
        "complete": False, "files": [],
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    for index, raw in enumerate(dict.fromkeys(paths)):
        check_file_cancelled()
        source = validate_media_path(raw, must_exist=True, require_file=True)
        backup = directory / f"{index:04d}-{source.name}"
        # Real copies also protect metadata subsequently written in place.
        with operation_kind("backup"):
            publish_file(source, backup, OrganizeMode.COPY, overwrite=True)
        manifest["files"].append({"source": raw, "backup": backup.name})
    manifest["complete"] = True
    temporary = directory / "manifest.tmp"
    temporary.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    temporary.replace(manifest_path)
    return directory


async def prepare_correction_backup(job, *, db_path=None) -> Path:
    from server.application.scrape_job_service import ScrapeJobService

    history = HistoryService(db_path=db_path)
    files = HistoryFileService(
        history, ScrapedFileService(db_path=db_path), ConfigService(db_path=db_path),
        ScrapeJobService(db_path=db_path),
    )
    listing = await files.list_files(job.correction_history_id, include_shared_metadata=True)
    paths = [job.file_path]
    paths.extend(
        item.path for item in listing.files
        if item.exists and item.role in {HistoryFileRole.ORGANIZED, HistoryFileRole.METADATA}
    )
    return await run_file_io(_save_snapshot, job.id, job.correction_history_id, paths)


def _cleanup_expired(now: float) -> int:
    if not BACKUP_ROOT.is_dir() or BACKUP_ROOT.is_symlink():
        return 0
    removed = 0
    for directory in BACKUP_ROOT.iterdir():
        check_file_cancelled()
        if directory.is_symlink() or not directory.is_dir():
            continue
        try:
            manifest = json.loads((directory / "manifest.json").read_text())
            expired = float(manifest["expires_at"]) <= now
        except (OSError, ValueError, KeyError, TypeError):
            continue  # Unknown content is never treated as owned disposable data.
        if expired:
            try:
                shutil.rmtree(directory)
                removed += 1
            except OSError:
                logger.exception("纠正备份清理失败: %s", directory)
    return removed


async def cleanup_expired_correction_backups() -> int:
    return await run_file_io(_cleanup_expired, time.time())
