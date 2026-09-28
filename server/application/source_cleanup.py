"""Safe cleanup helpers for successfully moved local source files."""

from pathlib import Path

from server.common.path_security import allowed_media_roots, validate_media_path


def remove_empty_source_parent(source_path: str) -> bool:
    """Remove only the immediate empty parent of a local source file.

    The configured media roots themselves are never removed.  No recursive
    deletion is performed, so unrelated files or directories are untouched.
    """
    source = Path(source_path)
    parent = validate_media_path(str(source.parent))
    if not parent.is_dir() or parent in set(allowed_media_roots()):
        return False

    try:
        next(parent.iterdir())
    except StopIteration:
        parent.rmdir()
        return True
    return False
