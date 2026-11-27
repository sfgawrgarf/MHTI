"""Publish complete files without destroying an existing destination on failure."""

import os
import shutil
import errno
import ctypes
import sys
import uuid
from pathlib import Path
from tempfile import TemporaryDirectory
from contextlib import contextmanager
from threading import Lock, RLock
from weakref import WeakValueDictionary

from server.infrastructure import file_journal
from server.models.organize import OrganizeMode
from server.common.file_cancellation import check_file_cancelled


class _PathLock:
    def __init__(self):
        self.lock = RLock()


_path_locks: WeakValueDictionary[str, _PathLock] = WeakValueDictionary()
_path_locks_guard = Lock()


@contextmanager
def path_operation_lock(path: Path):
    """Serialize only short publication/deletion steps, never large copies or awaits."""
    key = os.path.normcase(os.path.abspath(path))
    with _path_locks_guard:
        holder = _path_locks.get(key)
        if holder is None:
            holder = _PathLock()
            _path_locks[key] = holder
    with holder.lock:
        yield


def _publish_no_replace(staged: Path, destination: Path) -> None:
    if os.name == "nt":
        # Windows rename fails if the destination already exists.
        os.rename(staged, destination)
        return
    if sys.platform.startswith("linux"):
        # Unlike plain rename, RENAME_NOREPLACE is atomic and works on Linux
        # filesystems which support rename but not hardlinks (e.g. exFAT).
        rename = getattr(ctypes.CDLL(None, use_errno=True), "renameat2", None)
        if rename is not None:
            rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
            rename.restype = ctypes.c_int
            if rename(-100, os.fsencode(staged), -100, os.fsencode(destination), 1) == 0:
                return
            error = ctypes.get_errno()
            if error not in {errno.ENOSYS, errno.EINVAL, errno.EOPNOTSUPP}:
                raise OSError(error, os.strerror(error), str(destination))
    # Safe fallback: never replace a competing writer's destination.
    os.link(staged, destination, follow_symlinks=False)


def remove_moved_source(source: Path, expected: dict, operation_id: str | None = None) -> None:
    """Claim the directory entry before unlinking it, even with external writers."""
    operation_id = operation_id or uuid.uuid4().hex
    held_dir = source.parent / f".mhti-move-{operation_id}"
    held = held_dir / "source.tmp"
    with path_operation_lock(source):
        if held_dir.is_symlink():
            raise file_journal.RecoveryConflict(f"移动暂存目录已被替换: {held_dir}")
        if not held.exists() and not held.is_symlink():
            current = file_journal.signature(source)
            if current is None:
                if held_dir.is_dir():
                    held_dir.rmdir()
                return
            if expected is None or current != expected:
                raise file_journal.RecoveryConflict(f"移动源文件已变化，保留当前文件: {source}")
            held_dir.mkdir(mode=0o700, exist_ok=True)
            os.rename(source, held)
        if file_journal.signature(held) != expected:
            # If another writer recreated the source, preserve both entries.
            try:
                _publish_no_replace(held, source)
            except OSError as exc:
                raise file_journal.RecoveryConflict(f"移动源发生竞争，保留文件: {held}") from exc
            held.unlink(missing_ok=True)
            held_dir.rmdir()
            raise file_journal.RecoveryConflict(f"移动源已变化，已恢复到原路径: {source}")
        held.unlink()
        held_dir.rmdir()


def publish_file(source: Path, destination: Path, mode: OrganizeMode | None,
                 *, overwrite: bool = False) -> None:
    check_file_cancelled()
    mode = mode or OrganizeMode.MOVE
    receipt = file_journal.begin_publish(source, destination, mode)
    source_signature = file_journal.signature(source) if mode == OrganizeMode.MOVE else None
    if receipt and 'source_signature' in receipt:
        source_signature = receipt['source_signature']
    if source == destination:
        file_journal.prepared(receipt, source)
        file_journal.published(receipt)
        file_journal.completed(receipt)
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Stage on the destination filesystem. Even MOVE retains its source until
    # publication succeeds, including cross-device moves and failed replaces.
    with TemporaryDirectory(prefix=".mhti-publish-", dir=destination.parent) as staging:
        # Do not expose a video/subtitle suffix to concurrent media scanners.
        staged = Path(staging) / "payload.tmp"
        file_journal.stage_directory(receipt, Path(staging))
        if mode == OrganizeMode.HARDLINK:
            os.link(source, staged)
        elif mode == OrganizeMode.SYMLINK:
            os.symlink(source, staged)
        elif mode == OrganizeMode.MOVE:
            try:
                # Preserve same-filesystem rename performance and inode identity.
                os.link(source, staged)
            except OSError as exc:
                if exc.errno not in {errno.EXDEV, errno.EPERM, errno.EOPNOTSUPP}:
                    raise
                file_journal.copy_started(receipt, staged)
                shutil.copy2(source, staged)
        else:
            file_journal.copy_started(receipt, staged)
            shutil.copy2(source, staged)
        file_journal.prepared(receipt, staged)
        check_file_cancelled()
        with path_operation_lock(destination):
            if overwrite:
                os.replace(staged, destination)
            else:
                # Atomic no-clobber publication, including concurrent writers.
                _publish_no_replace(staged, destination)
        file_journal.published(receipt)
        if mode == OrganizeMode.MOVE:
            remove_moved_source(source, source_signature, receipt.get('id') if receipt else None)
        file_journal.completed(receipt)
