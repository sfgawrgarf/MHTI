"""Regression tests for durable, thread-safe database log batching."""

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

from server.core.log_handler import DatabaseLogHandler
from server.services.log_service import LogService


def _record(message: str) -> logging.LogRecord:
    return logging.LogRecord(
        name="test.database-log",
        level=logging.WARNING,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=(),
        exc_info=None,
    )


@pytest.mark.asyncio
async def test_failed_flush_restores_batch_before_newer_entries():
    log_service = Mock()
    log_service.batch_insert = AsyncMock()
    handler = DatabaseLogHandler(log_service)

    attempts = 0

    async def insert_with_transient_failure(entries):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            handler.emit(_record("second"))
            raise RuntimeError("database unavailable")

    log_service.batch_insert.side_effect = insert_with_transient_failure
    handler.emit(_record("first"))

    assert await handler._flush() is False
    assert handler.pending_count == 2

    assert await handler._flush() is True

    assert handler.pending_count == 0
    written = log_service.batch_insert.await_args_list[1].args[0]
    assert [entry["message"] for entry in written] == ["first", "second"]


@pytest.mark.asyncio
async def test_shutdown_retries_and_waits_for_final_flush():
    log_service = Mock()
    log_service.batch_insert = AsyncMock(
        side_effect=[RuntimeError("database unavailable"), None]
    )
    handler = DatabaseLogHandler(log_service, flush_interval=60)
    handler.start()
    handler.emit(_record("shutdown entry"))

    assert await handler.aclose(retries=2) is True

    assert handler.pending_count == 0
    assert log_service.batch_insert.await_count == 2


@pytest.mark.asyncio
async def test_concurrent_logging_threads_do_not_lose_entries():
    log_service = Mock()
    log_service.batch_insert = AsyncMock()
    handler = DatabaseLogHandler(log_service, batch_size=1000)

    messages = [f"thread-entry-{index}" for index in range(200)]
    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(lambda message: handler.emit(_record(message)), messages))

    assert handler.pending_count == len(messages)
    assert await handler._flush() is True

    written = log_service.batch_insert.await_args.args[0]
    assert len(written) == len(messages)
    assert {entry["message"] for entry in written} == set(messages)


@pytest.mark.asyncio
async def test_log_service_propagates_database_write_failure(monkeypatch):
    database = AsyncMock()
    database.executemany.side_effect = RuntimeError("database unavailable")
    connection = AsyncMock()
    connection.__aenter__.return_value = database
    manager = Mock()
    manager.get_connection = MagicMock(return_value=connection)
    monkeypatch.setattr(
        "server.services.log_service.get_db_manager",
        AsyncMock(return_value=manager),
    )
    service = LogService()
    entry = {
        "timestamp": datetime.now(),
        "level": "WARNING",
        "logger": "test",
        "message": "must be retried",
        "extra_data": None,
        "request_id": None,
        "user_id": None,
    }

    with pytest.raises(RuntimeError, match="database unavailable"):
        await service.batch_insert([entry])
