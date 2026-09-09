"""Stage file transfers before publishing a complete destination."""

import logging
import os
import shutil
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory

from server.models.organize import OrganizeMode
from server.core.path_security import validate_media_path

logger = logging.getLogger(__name__)


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

    MOVE deliberately stages a copy: even a cross-device transfer or failed
    final replacement must not lose the original. Temporary data stays on the
    destination filesystem. No-overwrite publication uses an exclusive link,
    so a destination appearing concurrently is never silently replaced.
    """
    if source == destination:
        return
    with TemporaryDirectory(prefix=".mhti-transfer-", dir=destination.parent) as directory:
        staged = Path(directory) / "payload"
        if mode == OrganizeMode.HARDLINK:
            os.link(source, staged)
        elif mode == OrganizeMode.SYMLINK:
            os.symlink(source, staged)
        else:
            shutil.copy2(source, staged)

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
