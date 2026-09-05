"""Base repository for data access layer."""

from abc import ABC, abstractmethod
from typing import Generic, TypeVar

T = TypeVar("T")


class BaseRepository(ABC, Generic[T]):
    """
    Abstract base class for repositories.

    Provides standard CRUD operations for data access.
    """

    @abstractmethod
    async def get_by_id(self, id: int) -> T | None:
        """Get entity by ID."""
        ...

    @abstractmethod
    async def get_all(self) -> list[T]:
        """Get all entities."""
        ...

    @abstractmethod
    async def create(self, entity: T) -> T:
        """Create a new entity."""
        ...

    @abstractmethod
    async def update(self, entity: T) -> T:
        """Update an existing entity."""
        ...

    @abstractmethod
    async def delete(self, id: int) -> bool:
        """Delete an entity by ID."""
        ...
