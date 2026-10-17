"""Durable file-operation receipts. No receipt is inferred from a filename alone."""

from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import time
import uuid

from server.common.path_security import validate_media_file_entry, validate_media_path
from server.infrastructure.config import DATA_DIR

owner: ContextVar[str | None] = ContextVar('file_operation_owner', default=None)
kind: ContextVar[str] = ContextVar('file_operation_kind', default='media')
details: ContextVar[dict] = ContextVar('file_operation_details', default={})
_metadata_write: ContextVar[tuple | None] = ContextVar('metadata_write', default=None)


class RecoveryConflict(ValueError):
    """A recovery cannot prove ownership; preserve all files for inspection."""


@contextmanager
def operation_kind(value: str):
    token = kind.set(value)
    try:
        yield
    finally:
        kind.reset(token)


def root() -> Path:
    path = Path(os.environ.get('MHTI_FILE_JOURNAL_DIR') or DATA_DIR / 'file-operations')
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink():
        raise RecoveryConflict('文件操作恢复目录不能是软链接')
    return path


def signature(path: Path) -> dict | None:
    try:
        stat = path.lstat()
    except FileNotFoundError:
        return None
    return {'dev': stat.st_dev, 'ino': stat.st_ino, 'size': stat.st_size, 'mtime': stat.st_mtime_ns}


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def save(record: dict) -> None:
    directory = root()
    name = record['id']
    if not re.fullmatch(r'[0-9a-f]{32}', name):
        raise RecoveryConflict('无效的恢复记录 ID')
    target = directory / f'{name}.json'
    temporary = directory / f'.{name}-{uuid.uuid4().hex}.tmp'
    try:
        with temporary.open('x', encoding='utf-8') as handle:
            os.chmod(temporary, 0o600)
            json.dump(record, handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        temporary.unlink(missing_ok=True)


def new_record(record_kind: str, **fields) -> dict:
    record = {'id': uuid.uuid4().hex, 'owner': owner.get(), 'kind': record_kind,
              'created': time.time_ns(), 'phase': 'intent', **fields}
    save(record)
    return record


def begin_publish(source: Path, destination: Path, mode) -> dict | None:
    if _metadata_write.get() is not None:
        return {'metadata_write': _metadata_write.get()}
    if owner.get() is None or kind.get() not in {'media', 'stage'}:
        return None
    return new_record(kind.get(), source=str(source), destination=str(destination),
                      mode=getattr(mode, 'value', mode), source_signature=signature(source),
                      details=details.get())


def prepared(record: dict | None, payload: Path) -> None:
    if record is not None and 'metadata_write' in record:
        plan, item = record['metadata_write']
        item['published_signature'] = signature(payload)
        save(plan)  # Persist ownership BEFORE atomic publication.
        return
    if record is not None:
        record.update(phase='prepared', payload=str(payload), payload_signature=signature(payload))
        save(record)


def published(record: dict | None) -> None:
    if record is not None and 'metadata_write' not in record:
        record['phase'] = 'published'
        save(record)


def completed(record: dict | None) -> None:
    if record is not None and 'metadata_write' not in record:
        record['phase'] = 'complete'
        save(record)


def read_records(job_id: str | None = None) -> list[dict]:
    records = []
    for path in root().glob('*.json'):
        if path.is_symlink():
            raise RecoveryConflict('恢复记录不能是软链接')
        record = json.loads(path.read_text(encoding='utf-8'))
        if path.stem != record.get('id'):
            raise RecoveryConflict('恢复记录身份不一致')
        if job_id is None or record.get('owner') == job_id:
            records.append(record)
    return sorted(records, key=lambda item: item['created'])


def forget(record: dict) -> None:
    (root() / f"{record['id']}.json").unlink(missing_ok=True)


def owns_destination(record: dict) -> bool:
    expected = record.get('payload_signature')
    if expected is None:
        return False
    raw = Path(record['destination'])
    validate_media_path(str(raw))
    if not raw.exists() and not raw.is_symlink():
        return False
    path = validate_media_file_entry(str(raw))
    return signature(path) == expected


def restore_stage(record: dict) -> None:
    source = validate_media_path(record['source'])
    destination = validate_media_path(record['destination'])
    if not destination.exists():
        if signature(source) in (record.get('source_signature'), record.get('payload_signature')):
            forget(record)
            return
        raise RecoveryConflict(f'让位文件和原路径无法核对: {source} / {destination}')
    if not owns_destination(record):
        raise RecoveryConflict(f'让位文件归属不一致: {destination}')
    if source.exists() or source.is_symlink():
        if signature(source) not in (record.get('source_signature'), record.get('payload_signature')):
            raise RecoveryConflict(f'原位置已被其他文件占用，保留两侧: {source} / {destination}')
        destination.unlink()  # Both entries are proven copies of this operation.
    else:
        from server.infrastructure.file_operations import publish_file
        from server.models.organize import OrganizeMode
        with operation_kind('recovery'):
            publish_file(destination, source, OrganizeMode.MOVE)
    forget(record)


def finish_record(record: dict) -> None:
    """Remove only a proven leftover publication payload, never arbitrary content."""
    payload = Path(record.get('payload', ''))
    if payload.name == 'payload.tmp' and payload.parent.name.startswith('.mhti-publish-'):
        if signature(payload) == record.get('payload_signature'):
            validate_media_file_entry(str(payload)).unlink()
            try:
                payload.parent.rmdir()
            except OSError:
                pass
    forget(record)


class MetadataPlan:
    """Prepare metadata privately, then commit/recover each validated file."""

    def __init__(self, record: dict):
        self.record = record
        self.directory = Path(record['directory'])
        self.content = self.directory / 'content'

    @classmethod
    def create(cls, destination: Path, metadata_dir: str | None):
        base = validate_media_path(metadata_dir) if metadata_dir else destination.parent.parent.parent
        base.mkdir(parents=True, exist_ok=True)
        directory = base / f'.mhti-metadata-{uuid.uuid4().hex}'
        directory.mkdir(mode=0o700)
        (directory / 'originals').mkdir()
        final_series = (base / destination.parent.parent.name) if metadata_dir else destination.parent.parent
        final_season = final_series / destination.parent.name
        record = new_record('metadata', directory=str(directory), destination=str(destination),
                            final_series=str(final_series), final_season=str(final_season), seeds=[], files=[])
        plan = cls(record)
        series = plan.content / destination.parent.parent.name
        season = series / destination.parent.name
        season.mkdir(parents=True)
        for final_dir, stage_dir, season_level in ((final_series, series, False), (final_season, season, True)):
            if not final_dir.is_dir():
                continue
            entries = list(final_dir.iterdir())
            extras = final_dir / 'extrafanart'
            if not season_level and extras.is_dir() and not extras.is_symlink():
                entries.extend(extras.iterdir())
            for entry in entries:
                if not entry.is_file() or entry.suffix.lower() not in {'.nfo', '.jpg', '.jpeg', '.png', '.webp'}:
                    continue
                if season_level and entry.name != 'season.nfo' and not (entry.name.startswith('season') and entry.name.endswith('-poster.jpg')) and entry.stem not in {destination.stem, f'{destination.stem}-thumb'}:
                    continue
                if entry.is_symlink():
                    raise RecoveryConflict(f'元数据不能通过软链接覆盖: {entry}')
                source = validate_media_file_entry(str(entry))
                if source.is_symlink():
                    raise RecoveryConflict(f'元数据不能通过软链接覆盖: {source}')
                backup = directory / 'originals' / str(len(record['seeds']))
                shutil.copy2(source, backup)
                staged_file = stage_dir / entry.relative_to(final_dir)
                staged_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, staged_file)
                record['seeds'].append({'destination': str(source), 'backup': str(backup),
                                        'signature': signature(source), 'digest': digest(source)})
        save(record)
        return plan

    def prepare(self) -> None:
        seeds = {item['destination']: item for item in self.record['seeds']}
        files = []
        for source in self.content.rglob('*'):
            if not source.is_file():
                continue
            relative = source.relative_to(self.content)
            destination = Path(self.record['final_series']).parent / relative
            destination = validate_media_path(str(destination))
            seed = seeds.get(str(destination))
            checksum = digest(source)
            if seed and checksum == seed['digest']:
                continue
            files.append({'source': str(source), 'destination': str(destination), 'digest': checksum,
                          'before': seed, 'published_signature': None})
        self.record.update(files=files, phase='prepared')
        save(self.record)

    def commit(self) -> None:
        from server.infrastructure.file_operations import publish_file
        from server.models.organize import OrganizeMode
        for item in self.record['files']:
            source = validate_media_file_entry(item['source'])
            target = Path(item['destination'])
            validate_media_path(str(target))
            if target.is_symlink():
                raise RecoveryConflict(f'元数据目标变为软链接: {target}')
            if item.get('published_signature') and signature(target) == item['published_signature']:
                continue
            if target.exists() and digest(target) == item['digest']:
                continue  # Identical shared metadata may belong to another writer.
            before = item['before']
            if signature(target) != (before['signature'] if before else None):
                raise RecoveryConflict(f'元数据目标被其他任务修改: {target}')
            token = _metadata_write.set((self.record, item))
            try:
                with operation_kind('metadata'):
                    publish_file(source, target, OrganizeMode.COPY, overwrite=before is not None)
            finally:
                _metadata_write.reset(token)
            save(self.record)
        self.record['phase'] = 'complete'
        save(self.record)

    def rollback(self) -> None:
        from server.infrastructure.file_operations import publish_file
        from server.models.organize import OrganizeMode
        for item in reversed(self.record['files']):
            target = Path(item['destination'])
            validate_media_path(str(target))
            if target.is_symlink():
                raise RecoveryConflict(f'元数据目标变为软链接: {target}')
            if not target.exists():
                continue
            own = item.get('published_signature')
            if own is None or signature(target) != own:
                continue  # Never replace another writer's file.
            before = item['before']
            if before:
                backup = validate_media_file_entry(before['backup'])
                with operation_kind('metadata'):
                    publish_file(backup, target, OrganizeMode.COPY, overwrite=True)
            else:
                target.unlink()
        self.cleanup()

    def cleanup(self) -> None:
        safe = validate_media_path(str(self.directory))
        if self.directory.is_symlink() or not safe.name.startswith('.mhti-metadata-'):
            raise RecoveryConflict('元数据暂存目录身份不一致')
        if safe.exists():
            shutil.rmtree(safe, ignore_errors=False)
        forget(self.record)
