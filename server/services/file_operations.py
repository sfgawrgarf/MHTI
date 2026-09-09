"""Stage file transfers before publishing a complete destination."""

import logging
import errno
import os
import shutil
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory

from server.models.organize import OrganizeMode
from server.core.path_security import validate_media_path
from server.services.file_io import check_file_cancelled

logger = logging.getLogger(__name__)


def copy_file(source: Path, destination: Path) -> None:
    """Copy with bounded memory and cancellation checkpoints before publication."""
    with source.open("rb") as reader, destination.open("wb") as writer:
        while True:
            check_file_cancelled()
            chunk = reader.read(1024 * 1024)
            if not chunk:
                break
            writer.write(chunk)
    check_file_cancelled()
    shutil.copystat(source, destination)


def write_metadata_text(path: Path, content: str) -> None:
    """Validate the final sidecar, including existing symlinks, before writing."""
    validate_media_path(str(path))
    temporary: Path | None = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                prefix=".mhti-nfo-", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(content)
        temporary.chmod(path.stat().st_mode & 0o777 if path.exists() else 0o644)
        check_file_cancelled()
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def publish_file(
    source: Path,
    destination: Path,
    mode: OrganizeMode,
    *,
    overwrite: bool = False,
) -> None:
    """Leave the source and old destination intact until publication succeeds.

    MOVE stages a copy for overwrites/cross-device transfers and a hard link
    for same-filesystem non-overwriting moves. A failed final publication must
    not lose the original. Temporary data stays on the
    destination filesystem. No-overwrite publication uses an exclusive link,
    so a destination appearing concurrently is never silently replaced.
    """
    if source == destination:
        return
    check_file_cancelled()
    with TemporaryDirectory(prefix=".mhti-transfer-", dir=destination.parent) as directory:
        staged = Path(directory) / "payload"
        if mode == OrganizeMode.HARDLINK:
            os.link(source, staged)
        elif mode == OrganizeMode.SYMLINK:
            os.symlink(source, staged)
        elif mode == OrganizeMode.MOVE and not overwrite:
            # Same-filesystem moves need no full copy; publication is still
            # exclusive and the source is only unlinked after success.
            try:
                os.link(source, staged)
            except OSError as exc:
                if exc.errno not in (errno.EXDEV, errno.EPERM, errno.EOPNOTSUPP):
                    raise
                copy_file(source, staged)
        else:
            copy_file(source, staged)

        check_file_cancelled()
        # Once committed, finish source cleanup even if cancellation arrives.
        if overwrite:
            os.replace(staged, destination)
        elif mode == OrganizeMode.SYMLINK:
            os.symlink(source, destination)
        else:
            os.link(staged, destination)

    if mode == OrganizeMode.MOVE:
        try:
            source.unlink()
        except OSError:
            # Publication has committed. Keep the completed destination and
            # report the retained source, rather than suggesting a blind retry.
            logger.warning("目标已完整写入，但源文件未能删除，已保留源文件: %s", source, exc_info=True)
