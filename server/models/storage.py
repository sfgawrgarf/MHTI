"""Storage locator models."""

from enum import Enum

from pydantic import BaseModel


P115_VIRTUAL_ROOT_PATH = "/115网盘"


def is_p115_virtual_path(path: str) -> bool:
    """Return whether a path belongs to the provider-only 115 namespace."""
    normalized = path.rstrip("/")
    return normalized == P115_VIRTUAL_ROOT_PATH or normalized.startswith(
        f"{P115_VIRTUAL_ROOT_PATH}/"
    )


def validate_locator_namespace(locator: "StorageLocator") -> None:
    """Ensure a locator path belongs to the namespace of its provider."""
    path_is_p115 = is_p115_virtual_path(locator.path)
    if locator.provider == StorageProvider.P115 and not path_is_p115:
        raise ValueError("115 存储定位必须使用 /115网盘 路径")
    if locator.provider == StorageProvider.LOCAL and path_is_p115:
        raise ValueError("115 网盘路径不能声明为本地存储")


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
