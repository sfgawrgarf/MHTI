"""Storage locator models."""

from enum import Enum

from pydantic import BaseModel


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
