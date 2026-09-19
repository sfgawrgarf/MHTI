"""Regression tests for application lifespan cleanup."""

from unittest.mock import AsyncMock

import pytest

import server.main as main


@pytest.mark.asyncio
async def test_startup_failure_still_runs_shutdown(monkeypatch):
    init_database = AsyncMock(side_effect=RuntimeError("database startup failed"))
    shutdown = AsyncMock()
    monkeypatch.setattr(main, "init_database", init_database)
    monkeypatch.setattr(main, "_shutdown_application", shutdown)

    with pytest.raises(RuntimeError, match="database startup failed"):
        async with main.lifespan(main.app):
            pytest.fail("lifespan yielded after startup failure")

    init_database.assert_awaited_once_with()
    shutdown.assert_awaited_once_with(None)
