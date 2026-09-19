"""Tests for applying persisted logging settings at runtime."""

import logging
from unittest.mock import AsyncMock, Mock

import pytest

from server.core.logging_runtime import LoggingRuntime
from server.models.log import LogConfig, LogLevel


@pytest.mark.asyncio
async def test_runtime_applies_and_disables_owned_handlers(tmp_path, monkeypatch):
    monkeypatch.setattr("server.core.logging_runtime.LOG_DIR", tmp_path)
    root = logging.getLogger()
    original_level = root.level
    runtime = LoggingRuntime()
    log_service = Mock()
    log_service.batch_insert = AsyncMock()

    try:
        runtime.bootstrap()
        await runtime.apply(
            LogConfig(
                log_level=LogLevel.ERROR,
                console_enabled=False,
                file_enabled=True,
                db_enabled=True,
                max_file_size_mb=2,
                max_file_count=3,
                realtime_enabled=False,
            ),
            log_service,
        )

        assert root.level == logging.ERROR
        assert runtime.console_handler not in root.handlers
        assert runtime.file_handler is not None
        assert runtime.file_handler.maxBytes == 2 * 1024 * 1024
        assert runtime.file_handler.backupCount == 3
        assert runtime.database_handler is not None
        assert runtime.database_handler.level == logging.ERROR

        await runtime.apply(
            LogConfig(
                console_enabled=True,
                file_enabled=False,
                db_enabled=False,
                realtime_enabled=False,
            ),
            log_service,
        )

        assert runtime.console_handler in root.handlers
        assert runtime.file_handler is None
        assert runtime.database_handler is None
    finally:
        await runtime.shutdown()
        if runtime.console_handler in root.handlers:
            root.removeHandler(runtime.console_handler)
        root.setLevel(original_level)
