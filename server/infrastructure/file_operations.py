"""Publish complete files without destroying an existing destination on failure."""

import os
import shutil
import errno
import ctypes
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from server.models.organize import OrganizeMode
from server.common.file_cancellation import check_file_cancelled


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


def publish_file(source: Path, destination: Path, mode: OrganizeMode | None,
                 *, overwrite: bool = False) -> None:
    check_file_cancelled()
    if source == destination:
        return
    mode = mode or OrganizeMode.MOVE
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Stage on the destination filesystem. Even MOVE retains its source until
    # publication succeeds, including cross-device moves and failed replaces.
    with TemporaryDirectory(prefix=".mhti-publish-", dir=destination.parent) as staging:
        # Do not expose a video/subtitle suffix to concurrent media scanners.
        staged = Path(staging) / "payload.tmp"
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
                shutil.copy2(source, staged)
        else:
            shutil.copy2(source, staged)
        check_file_cancelled()
        if overwrite:
            os.replace(staged, destination)
        else:
            # Atomic no-clobber publication, including concurrent writers.
            _publish_no_replace(staged, destination)
        if mode == OrganizeMode.MOVE:
            source.unlink()
