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


def infer_directory_locator(
    path: str | None,
    locator: StorageLocator | None,
    *,
    allow_file: bool = False,
) -> StorageLocator | None:
    """Return a provider-aware directory locator for a configured path.

    API clients are allowed to submit a plain local path.  Normalizing it here
    keeps provider decisions consistent and avoids treating a 115 virtual path
    as a local filesystem destination later in the worker.
    """
    if locator is not None:
        if not locator.path.strip():
            raise ValueError("存储定位路径不能为空")
        if path and locator.path.rstrip("/") != path.rstrip("/"):
            raise ValueError("存储定位信息与所选路径不一致")
        if not allow_file and not locator.is_dir:
            raise ValueError("输出和元数据存储定位必须是目录")
        return locator
    if not path:
        return None
    provider = (
        StorageProvider.P115
        if is_p115_virtual_path(path)
        else StorageProvider.LOCAL
    )
    return StorageLocator(provider=provider, path=path, is_dir=True)


def normalize_file_locator(
    path: str,
    locator: StorageLocator | None,
) -> StorageLocator | None:
    """Validate a source-file locator and drop redundant local locators."""
    if locator is None:
        return None
    if not locator.path.strip():
        raise ValueError("源文件存储定位路径不能为空")
    if locator.path.rstrip("/") != path.rstrip("/"):
        raise ValueError("存储定位信息与源文件路径不一致")
    if locator.provider == StorageProvider.LOCAL:
        return None
    if locator.is_dir:
        raise ValueError("115 源存储定位必须是文件")
    if not locator.file_id or locator.file_id == "0":
        raise ValueError("115 源文件缺少有效的 file_id")
    return locator


def is_p115_to_local(
    *,
    source_path: str,
    source_locator: StorageLocator | None,
    target_path: str | None,
    target_locator: StorageLocator | None,
) -> bool:
    """Return whether a selection downloads a 115 source to local storage."""
    source_is_p115 = (
        source_locator.provider == StorageProvider.P115
        if source_locator is not None
        else is_p115_virtual_path(source_path)
    )
    target_is_p115 = (
        target_locator.provider == StorageProvider.P115
        if target_locator is not None
        else bool(target_path and is_p115_virtual_path(target_path))
    )
    return source_is_p115 and not target_is_p115


def validate_storage_capabilities(
    *,
    source_path: str,
    source_locator: StorageLocator | None,
    target_path: str | None,
    target_locator: StorageLocator | None,
    metadata_locator: StorageLocator | None,
    allow_local_output: bool,
    organize_mode: object | None,
) -> None:
    """Reject storage combinations that the scraper cannot execute safely."""
    source_provider = (
        source_locator.provider
        if source_locator is not None
        else (
            StorageProvider.P115
            if is_p115_virtual_path(source_path)
            else StorageProvider.LOCAL
        )
    )
    target_provider = (
        target_locator.provider
        if target_locator is not None
        else (
            StorageProvider.P115
            if target_path and is_p115_virtual_path(target_path)
            else StorageProvider.LOCAL
        )
    )

    if metadata_locator and metadata_locator.provider != StorageProvider.LOCAL:
        raise ValueError("元数据目录仅支持本地媒体目录")

    if source_provider == StorageProvider.P115 and source_locator is None:
        raise ValueError("115 源文件缺少存储定位信息，请重新选择来源")

    if (
        source_provider == StorageProvider.P115
        and not target_path
        and target_locator is None
    ):
        raise ValueError("115 源文件必须指定输出目录")

    if source_provider == StorageProvider.LOCAL and target_provider == StorageProvider.P115:
        raise ValueError("暂不支持将本地文件输出到 115 网盘")

    mode_value = (
        "move"
        if organize_mode is None
        else getattr(organize_mode, "value", organize_mode)
    )
    supported_provider_modes = {"copy", "move", 2, 3}
    if (
        source_provider == StorageProvider.P115
        and mode_value not in supported_provider_modes
    ):
        raise ValueError("115 源文件仅支持复制或移动整理模式")

    if (
        source_provider == StorageProvider.P115
        and target_provider == StorageProvider.LOCAL
        and not allow_local_output
    ):
        raise ValueError("115 文件输出到本地前必须开启“允许下载到本地”")

    if (
        source_provider == StorageProvider.P115
        and target_provider == StorageProvider.LOCAL
        and mode_value not in {"copy", 3}
    ):
        raise ValueError("115 文件下载到本地仅支持复制模式")
