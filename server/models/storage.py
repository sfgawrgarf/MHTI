"""Storage locator models."""

from enum import Enum

from pydantic import BaseModel

P115_VIRTUAL_ROOT_PATH = "/115网盘"


def is_p115_virtual_path(path: str) -> bool:
    """Return whether ``path`` is the 115 virtual root or one of its children."""
    normalized = path.rstrip("/")
    return normalized == P115_VIRTUAL_ROOT_PATH or normalized.startswith(
        f"{P115_VIRTUAL_ROOT_PATH}/"
    )


class StorageProvider(str, Enum):
    """Supported storage providers."""

    LOCAL = "local"
    P115 = "115"


class StorageLocator(BaseModel):
    """Provider-aware storage locator."""

    provider: StorageProvider
    path: str
    file_id: str | None = None
    parent_id: str | None = None
    is_dir: bool = True
