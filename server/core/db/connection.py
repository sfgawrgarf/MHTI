"""Database connection pool management."""

import asyncio
import logging
import weakref
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

import aiosqlite

logger = logging.getLogger(__name__)

# Project root and database path
_PROJECT_ROOT = Path(__file__).parent.parent.parent.parent.resolve()
DATABASE_PATH = _PROJECT_ROOT / "data" / "scraper.db"

# SQLite allows only one connection to change journal mode at a time. Keep the
# lock scoped to the active event loop so it is safe across pytest/application
# loop lifecycles while serializing concurrent connections within each loop.
_journal_mode_locks: weakref.WeakKeyDictionary[
    asyncio.AbstractEventLoop,
    asyncio.Lock,
] = weakref.WeakKeyDictionary()

# A shared budget also covers services using isolated/custom database paths.
MAX_CONNECTIONS = 5
CACHE_SIZE_KIB = 4096
_connection_slots: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def _get_connection_slots() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    if loop not in _connection_slots:
        _connection_slots[loop] = asyncio.Semaphore(MAX_CONNECTIONS)
    return _connection_slots[loop]


async def _open_connection(path: Path) -> aiosqlite.Connection:
    # Shield thread startup so cancellation cannot orphan an aiosqlite worker.
    async def connect():
        return await aiosqlite.connect(path)

    task = asyncio.create_task(connect())
    try:
        conn = await asyncio.shield(task)
    except asyncio.CancelledError:
        conn = await task
        await conn.close()
        raise
    try:
        await configure_connection(conn)
    except BaseException:
        await conn.close()
        raise
    return conn


def _get_journal_mode_lock() -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    lock = _journal_mode_locks.get(loop)
    if lock is None:
        lock = asyncio.Lock()
        _journal_mode_locks[loop] = lock
    return lock


class DatabaseManager:
    """
    Database connection manager with connection pooling.

    Implements a simple connection pool pattern for SQLite with proper
    initialization and cleanup lifecycle management.
    """

    _instance: "DatabaseManager | None" = None
    _lock: asyncio.Lock = asyncio.Lock()

    def __init__(self) -> None:
        self._initialized = False
        self._pool: list[aiosqlite.Connection] = []
        self._pool_size = 5
        self._in_use: set[object] = set()
        self._pool_lock = asyncio.Lock()
        self._closing = False
        self._idle = asyncio.Event()
        self._idle.set()
        self._loop = asyncio.get_running_loop()

    @classmethod
    async def get_instance(cls) -> "DatabaseManager":
        """Get singleton instance of DatabaseManager."""
        if cls._instance is None:
            async with cls._lock:
                if cls._instance is None:
                    instance = DatabaseManager()
                    await instance._initialize()
                    cls._instance = instance
        return cls._instance

    async def _initialize(self) -> None:
        """Initialize database and create tables."""
        from server.core.db.schema import create_all_tables

        if self._initialized:
            return

        logger.info(f"Database path: {DATABASE_PATH}")
        DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)

        async with aiosqlite.connect(DATABASE_PATH) as db:
            await configure_connection(db)
            await db.execute("BEGIN IMMEDIATE")
            await create_all_tables(db)
            await db.commit()

        self._initialized = True
        logger.info("Database initialized successfully")

    @asynccontextmanager
    async def get_connection(self) -> AsyncGenerator[aiosqlite.Connection, None]:
        """
        Get a database connection from the pool.

        Usage:
            async with db_manager.get_connection() as db:
                await db.execute(...)
        """
        async with _get_connection_slots():
            async with self._pool_lock:
                if self._closing:
                    raise RuntimeError("Database pool is shutting down")
                conn = self._pool.pop() if self._pool else None
                # Reserve a borrower before opening so shutdown waits for it.
                self._idle.clear()
                marker = object()
                self._in_use.add(marker)

            try:
                if conn is None:
                    conn = await _open_connection(DATABASE_PATH)
                conn.row_factory = aiosqlite.Row
                yield conn
            finally:
                async def release():
                    reusable = False
                    try:
                        if conn is not None:
                            # Never lend an unfinished transaction to another task.
                            await conn.rollback()
                            conn.row_factory = aiosqlite.Row
                            reusable = True
                    finally:
                        async with self._pool_lock:
                            try:
                                if conn is not None:
                                    if reusable and not self._closing and len(self._pool) < self._pool_size:
                                        self._pool.append(conn)
                                    else:
                                        await conn.close()
                            finally:
                                self._in_use.discard(marker)
                                if not self._in_use:
                                    self._idle.set()

                cleanup = asyncio.create_task(release())
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    await cleanup
                    raise

    async def close_all(self) -> None:
        """Stop lending, drain borrowers, then close the idle connections."""
        async with self._pool_lock:
            self._closing = True
        await self._idle.wait()
        async with self._pool_lock:
            for conn in self._pool:
                await conn.close()
            self._pool.clear()
        logger.info("All database connections closed")


@asynccontextmanager
async def db_connection(path: Path) -> AsyncGenerator[aiosqlite.Connection, None]:
    """Use the application pool, or a bounded short-lived custom connection.

    Custom paths remain independent and do not retain threads after their scope.
    All callers retain responsibility for explicitly committing their writes.
    """
    manager = DatabaseManager._instance
    if (
        Path(path).resolve() == DATABASE_PATH.resolve()
        and manager is not None
        and manager._initialized
        and manager._loop is asyncio.get_running_loop()
    ):
        async with manager.get_connection() as conn:
            yield conn
        return

    async with _get_connection_slots():
        conn = await _open_connection(path)
        try:
            yield conn
        finally:
            cleanup = asyncio.create_task(conn.close())
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                await cleanup
                raise


async def configure_connection(db: aiosqlite.Connection) -> None:
    """Configure database connection with optimal settings."""
    # Configure the busy handler before requesting WAL mode. Switching a new
    # database to WAL takes a write lock, so concurrent first-use connections
    # must be able to wait instead of failing immediately with "database is
    # locked".
    await db.execute("PRAGMA busy_timeout=30000")
    async with _get_journal_mode_lock():
        await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA synchronous=NORMAL")
    await db.execute(f"PRAGMA cache_size=-{CACHE_SIZE_KIB}")  # 4 MiB per connection
    await db.execute("PRAGMA temp_store=FILE")


# Singleton access functions
async def get_db_manager() -> DatabaseManager:
    """Get the database manager singleton."""
    return await DatabaseManager.get_instance()


@asynccontextmanager
async def db_context() -> AsyncGenerator[aiosqlite.Connection, None]:
    """获取数据库连接的简化上下文管理器。

    Usage:
        async with db_context() as db:
            await db.execute(...)

    比直接使用 get_db_manager().get_connection() 更简洁。
    """
    manager = await get_db_manager()
    async with manager.get_connection() as db:
        yield db


async def get_db() -> AsyncGenerator[aiosqlite.Connection, None]:
    """
    Get database connection (FastAPI dependency compatible).

    Usage in API routes:
        @router.get("/")
        async def handler(db: aiosqlite.Connection = Depends(get_db)):
            ...
    """
    manager = await get_db_manager()
    async with manager.get_connection() as db:
        yield db


async def init_database() -> None:
    """Initialize the database (called at application startup)."""
    await get_db_manager()


async def close_database() -> None:
    """Close all database connections (called at application shutdown)."""
    if DatabaseManager._instance:
        await DatabaseManager._instance.close_all()
        DatabaseManager._instance = None
