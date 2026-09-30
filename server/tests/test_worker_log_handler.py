"""Worker warnings must reach the event-loop-owned database handler."""

import asyncio
import logging
import threading
from unittest.mock import AsyncMock

import pytest

from server.infrastructure.log_handler import DatabaseLogHandler


@pytest.mark.asyncio
async def test_concurrent_worker_logs_are_flushed_without_errors(monkeypatch):
    loop_thread = threading.get_ident()
    persisted = []

    async def insert(entries):
        assert threading.get_ident() == loop_thread
        persisted.extend(entries)

    service = AsyncMock()
    service.batch_insert.side_effect = insert
    handler = DatabaseLogHandler(service, batch_size=5, flush_interval=3600)
    errors = []
    monkeypatch.setattr(handler, "handleError", errors.append)
    handler.start()

    def emit_many(worker):
        for number in range(50):
            record = logging.LogRecord("worker", logging.WARNING, __file__, 0,
                                       f"{worker}:{number}", (), None)
            handler.handle(record)

    try:
        await asyncio.gather(*(asyncio.to_thread(emit_many, worker) for worker in range(4)))
        await asyncio.sleep(0)
        await handler._flush()
        assert errors == []
        assert len(persisted) == 200
        assert {entry["message"] for entry in persisted} == {
            f"{worker}:{number}" for worker in range(4) for number in range(50)
        }
    finally:
        handler.stop()
        await asyncio.sleep(0)
        handler.close()
