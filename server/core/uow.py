"""Unit of Work pattern for transaction management."""


from server.core.database import get_db_manager


class UnitOfWork:
    """
    Unit of Work pattern implementation for managing database transactions.

    Usage:
        async with UnitOfWork() as uow:
            # perform operations
            await uow.commit()
    """

    def __init__(self):
        self._connection = None
        self._context = None

    async def __aenter__(self) -> "UnitOfWork":
        manager = await get_db_manager()
        self._context = manager.get_connection()
        self._connection = await self._context.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        try:
            if self._context is not None:
                await self._context.__aexit__(exc_type, exc_val, exc_tb)
        finally:
            self._connection = None
            self._context = None

    async def commit(self):
        """Commit the current transaction."""
        if self._connection:
            await self._connection.commit()

    async def rollback(self):
        """Rollback the current transaction."""
        if self._connection:
            await self._connection.rollback()

    @property
    def connection(self):
        """Get the current database connection."""
        return self._connection
