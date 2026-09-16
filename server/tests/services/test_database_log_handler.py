"""Regression tests for durable database log batching."""

from unittest.mock import AsyncMock, Mock

import pytest

from server.core.log_handler import DatabaseLogHandler


@pytest.mark.asyncio
async def test_failed_flush_restores_batch_before_newer_entries():
    log_service = Mock()
    log_service.batch_insert = AsyncMock(
        side_effect=[RuntimeError("database unavailable"), None]
    )
    handler = DatabaseLogHandler(log_service)
    first = {"message": "first"}
    second = {"message": "second"}
    handler._batch.append(first)

    await handler._flush()

    assert handler._batch == [first]
    handler._batch.append(second)

    await handler._flush()

    assert handler._batch == []
    assert log_service.batch_insert.await_args_list[1].args[0] == [first, second]
