"""Resource and migration regressions, executed by GitHub Actions."""

import asyncio
from contextlib import AsyncExitStack
from unittest.mock import AsyncMock

import aiosqlite
import pytest
import pytest_asyncio

from server.core.db import connection
from server.core.db.schema import (
    HISTORY_COLUMNS,
    MANUAL_JOB_COLUMNS,
    SCRAPE_JOB_COLUMNS,
    create_all_tables,
    migrate_history_table,
    migrate_manual_jobs_table,
    migrate_scrape_jobs_table,
)
from server.core.uow import UnitOfWork
from server.services import history_service, manual_job_service, scrape_job_service
from server.services.history_service import HistoryService
from server.services.manual_job_service import ManualJobService
from server.services.scrape_job_service import ScrapeJobService


@pytest_asyncio.fixture
async def manager(tmp_path, monkeypatch):
    monkeypatch.setattr(connection, "DATABASE_PATH", tmp_path / "pool.db")
    monkeypatch.setattr(connection.DatabaseManager, "_instance", None)
    manager = await connection.DatabaseManager.get_instance()
    try:
        yield manager
    finally:
        await manager.close_all()


@pytest.mark.asyncio
async def test_pool_limits_waiters_and_recovers_cancelled_waiter(manager):
    async with AsyncExitStack() as stack:
        connections = [
            await stack.enter_async_context(manager.get_connection())
            for _ in range(connection.MAX_CONNECTIONS)
        ]
        entered = asyncio.Event()

        async def borrow():
            async with manager.get_connection():
                entered.set()

        waiter = asyncio.create_task(borrow())
        await asyncio.sleep(0)
        assert not entered.is_set()
        assert len(manager._in_use) == connection.MAX_CONNECTIONS
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
    async with manager.get_connection() as db:
        assert db in connections
        assert (await (await db.execute("SELECT 1")).fetchone())[0] == 1


@pytest.mark.asyncio
async def test_cancelled_borrower_rolls_back_before_reuse(manager):
    ready = asyncio.Event()

    async def write():
        async with manager.get_connection() as db:
            await db.execute("INSERT INTO config (key, value) VALUES ('unfinished', 'value')")
            ready.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(write())
    await asyncio.wait_for(ready.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    async with manager.get_connection() as db:
        assert not db.in_transaction
        assert await (await db.execute("SELECT * FROM config WHERE key='unfinished'")).fetchone() is None
        assert (await (await db.execute("PRAGMA cache_size")).fetchone())[0] == -4096
        assert (await (await db.execute("PRAGMA temp_store")).fetchone())[0] == 1


@pytest.mark.asyncio
async def test_failed_connection_setup_releases_capacity(manager, monkeypatch):
    original = connection.configure_connection
    monkeypatch.setattr(connection, "configure_connection", AsyncMock(side_effect=RuntimeError("setup failed")))
    with pytest.raises(RuntimeError, match="setup failed"):
        async with manager.get_connection():
            pass
    assert not manager._in_use
    monkeypatch.setattr(connection, "configure_connection", original)
    async with manager.get_connection() as db:
        assert (await (await db.execute("SELECT 1")).fetchone())[0] == 1


@pytest.mark.asyncio
async def test_services_share_pool_and_custom_paths_share_budget(manager, tmp_path):
    async with manager.get_connection() as db:
        pooled = db
    async with connection.db_connection(connection.DATABASE_PATH) as db:
        assert db is pooled
    async with AsyncExitStack() as stack:
        for _ in range(connection.MAX_CONNECTIONS):
            await stack.enter_async_context(manager.get_connection())
        entered = asyncio.Event()

        async def custom():
            async with connection.db_connection(tmp_path / "custom.db"):
                entered.set()

        task = asyncio.create_task(custom())
        await asyncio.sleep(0)
        assert not entered.is_set()
    await asyncio.wait_for(task, 5)
    assert entered.is_set()


@pytest.mark.asyncio
async def test_shutdown_drains_borrower_and_rejects_new_work(manager):
    async with manager.get_connection() as db:
        shutdown = asyncio.create_task(manager.close_all())
        await asyncio.sleep(0)
        assert not shutdown.done()
        assert (await (await db.execute("SELECT 1")).fetchone())[0] == 1
    await asyncio.wait_for(shutdown, 5)
    assert not manager._pool
    assert not manager._in_use
    with pytest.raises(RuntimeError, match="shutting down"):
        async with manager.get_connection():
            pass


@pytest.mark.asyncio
async def test_unit_of_work_returns_connection_and_keeps_commits(manager):
    async with UnitOfWork() as uow:
        await uow.connection.execute("INSERT INTO config (key, value) VALUES ('committed', 'yes')")
        await uow.commit()
    assert not manager._in_use
    async with manager.get_connection() as db:
        assert (await (await db.execute("SELECT value FROM config WHERE key='committed'")).fetchone())[0] == "yes"


@pytest.mark.asyncio
async def test_old_history_migrates_without_losing_rows(tmp_path):
    path = tmp_path / "legacy.db"
    async with aiosqlite.connect(path) as db:
        await db.execute("""CREATE TABLE history_records (
            id TEXT PRIMARY KEY, task_name TEXT, folder_path TEXT, executed_at TEXT,
            status TEXT, total_files INTEGER, success_count INTEGER, failed_count INTEGER,
            duration_seconds REAL, error_message TEXT)""")
        await db.execute("INSERT INTO history_records (id, task_name) VALUES ('old', 'preserve me')")
        await create_all_tables(db)
        await db.commit()
        columns = {row[1] for row in await (await db.execute("PRAGMA table_info(history_records)")).fetchall()}
        assert {name for name, _ in HISTORY_COLUMNS} <= columns
        indexes = {
            row[0]
            for row in await (
                await db.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type = 'index' AND tbl_name = 'history_records'"
                )
            ).fetchall()
        }
        assert {
            "idx_history_executed_at",
            "idx_history_status_executed",
            "idx_history_manual_executed",
            "idx_history_scrape_job",
            "idx_history_fingerprint_status",
            "idx_history_folder_status",
        } <= indexes
        row = await (await db.execute("SELECT task_name, source FROM history_records WHERE id='old'")).fetchone()
        assert row == ("preserve me", "manual")
        statements = []
        await db.set_trace_callback(statements.append)
        await migrate_history_table(db)
        assert not any("ALTER TABLE" in sql for sql in statements)


@pytest.mark.asyncio
async def test_old_job_tables_migrate_once_without_losing_rows(tmp_path):
    path = tmp_path / "legacy-jobs.db"
    async with aiosqlite.connect(path) as db:
        await db.execute(
            """CREATE TABLE manual_jobs (
                id INTEGER PRIMARY KEY, scan_path TEXT, target_folder TEXT,
                link_mode INTEGER, created_at TEXT, status TEXT)"""
        )
        await db.execute(
            """CREATE TABLE scrape_jobs (
                id TEXT PRIMARY KEY, file_path TEXT, output_dir TEXT,
                source TEXT, source_id INTEGER, status TEXT, created_at TEXT,
                history_record_id TEXT)"""
        )
        await db.execute(
            "INSERT INTO manual_jobs VALUES (1, '/in', '/out', 2, '2026-01-01', 'success')"
        )
        await db.execute(
            "INSERT INTO scrape_jobs VALUES "
            "('job-1', '/in/a.mkv', '/out', 'manual', NULL, 'success', "
            "'2026-01-01', NULL)"
        )
        await create_all_tables(db)
        await db.commit()

        manual_columns = {
            row[1]
            for row in await (await db.execute("PRAGMA table_info(manual_jobs)")).fetchall()
        }
        scrape_columns = {
            row[1]
            for row in await (await db.execute("PRAGMA table_info(scrape_jobs)")).fetchall()
        }
        assert {name for name, _ in MANUAL_JOB_COLUMNS} <= manual_columns
        assert {name for name, _ in SCRAPE_JOB_COLUMNS} <= scrape_columns
        scrape_indexes = {
            row[0]
            for row in await (
                await db.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type = 'index' AND tbl_name = 'scrape_jobs'"
                )
            ).fetchall()
        }
        assert "idx_scrape_jobs_history_record" in scrape_indexes
        assert (
            await (await db.execute("SELECT scan_path FROM manual_jobs WHERE id = 1")).fetchone()
        )[0] == "/in"
        assert (
            await (await db.execute("SELECT file_path FROM scrape_jobs WHERE id = 'job-1'")).fetchone()
        )[0] == "/in/a.mkv"

        statements: list[str] = []
        await db.set_trace_callback(statements.append)
        await migrate_manual_jobs_table(db)
        await migrate_scrape_jobs_table(db)
        assert not any("ALTER TABLE" in sql for sql in statements)


@pytest.mark.asyncio
async def test_history_migration_once_per_custom_service_and_retry_on_error(tmp_path, monkeypatch):
    service = HistoryService(tmp_path / "custom.db")
    with pytest.raises(aiosqlite.OperationalError, match="Missing history_records"):
        await service._ensure_db()
    assert not service._db_ready
    async with aiosqlite.connect(service.db_path) as db:
        await create_all_tables(db)
        await db.commit()
    spy = AsyncMock(wraps=migrate_history_table)
    monkeypatch.setattr(history_service, "migrate_history_table", spy)
    await asyncio.gather(*(service.list_records() for _ in range(10)))
    assert spy.await_count == 1


@pytest.mark.asyncio
async def test_production_history_uses_startup_migration(manager, monkeypatch):
    monkeypatch.setattr(history_service, "DATABASE_PATH", connection.DATABASE_PATH)
    spy = AsyncMock(side_effect=AssertionError("unexpected runtime migration"))
    monkeypatch.setattr(history_service, "migrate_history_table", spy)
    # Also covers new service instances created for successive requests.
    for _ in range(3):
        records, total = await HistoryService().list_records()
        assert records == [] and total == 0
    spy.assert_not_awaited()


@pytest.mark.asyncio
async def test_production_job_services_use_startup_migrations(manager, monkeypatch):
    monkeypatch.setattr(manual_job_service, "DATABASE_PATH", connection.DATABASE_PATH)
    monkeypatch.setattr(scrape_job_service, "DATABASE_PATH", connection.DATABASE_PATH)
    manual_spy = AsyncMock(side_effect=AssertionError("unexpected runtime migration"))
    scrape_spy = AsyncMock(side_effect=AssertionError("unexpected runtime migration"))
    monkeypatch.setattr(manual_job_service, "migrate_manual_jobs_table", manual_spy)
    monkeypatch.setattr(scrape_job_service, "migrate_scrape_jobs_table", scrape_spy)

    for _ in range(3):
        manual_records, manual_total = await ManualJobService().list_jobs()
        scrape_records, scrape_total = await ScrapeJobService().list_jobs()
        assert manual_records == [] and manual_total == 0
        assert scrape_records == [] and scrape_total == 0

    manual_spy.assert_not_awaited()
    scrape_spy.assert_not_awaited()
