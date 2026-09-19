"""Regression tests for durable, thread-safe database log batching."""

import asyncio
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
async def test_failed_flush_keeps_restored_buffer_bounded():
    log_service = Mock()
    handler = DatabaseLogHandler(
        log_service,
        batch_size=3,
        max_buffer_size=3,
    )

    async def fail_after_new_entries(_entries):
        for index in range(3):
            handler.emit(_record(f"new-{index}"))
        raise RuntimeError("database unavailable")

    log_service.batch_insert = AsyncMock(side_effect=fail_after_new_entries)
    for index in range(3):
        handler.emit(_record(f"old-{index}"))

    assert await handler._flush() is False
    assert handler.pending_count == 3
    assert handler.dropped_count == 3
    assert [entry["message"] for entry in handler._batch] == [
        "new-0",
        "new-1",
        "new-2",
    ]


def test_database_handler_ignores_aiosqlite_debug_records():
    handler = DatabaseLogHandler(Mock())
    record = _record("executing database operation")
    record.name = "aiosqlite"
    record.levelno = logging.DEBUG
    record.levelname = "DEBUG"

    handler.emit(record)

    assert handler.pending_count == 0


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


def test_log_buffer_discards_oldest_entries_when_full():
    log_service = Mock()
    handler = DatabaseLogHandler(
        log_service,
        batch_size=10,
        max_buffer_size=10,
    )

    for index in range(15):
        handler.emit(_record(f"entry-{index}"))

    assert handler.pending_count == 10
    assert handler.dropped_count == 5
    assert [entry["message"] for entry in handler._batch] == [
        f"entry-{index}" for index in range(5, 15)
    ]


@pytest.mark.asyncio
async def test_stalled_database_write_times_out_and_restores_batch():
    log_service = Mock()

    async def never_finishes(_entries):
        await asyncio.Event().wait()

    log_service.batch_insert = never_finishes
    handler = DatabaseLogHandler(log_service, write_timeout=0.01)
    handler.emit(_record("retry later"))

    assert await handler._flush() is False
    assert handler.pending_count == 1


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


def test_extra_data_serialization_handles_non_json_values_and_cycles():
    cyclic: dict = {}
    cyclic["self"] = cyclic

    serialized_value = LogService._serialize_extra_data({"value": object()})
    serialized_cycle = LogService._serialize_extra_data(cyclic)

    assert serialized_value is not None
    assert "value" in serialized_value
    assert serialized_cycle is not None
    assert "unserializable" in serialized_cycle
