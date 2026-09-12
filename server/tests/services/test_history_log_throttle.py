"""History log persistence throttling regressions."""

from unittest.mock import AsyncMock

import aiosqlite
import pytest

from server.core.db import configure_connection, create_all_tables
from server.models.history import HistoryRecordCreate, ScrapeLogStep, TaskStatus
from server.services import history_service as history_module
from server.services.history_service import HistoryService


@pytest.mark.asyncio
async def test_live_log_updates_are_coalesced_and_terminal_snapshot_is_flushed(
    temp_db, monkeypatch
):
    async with aiosqlite.connect(temp_db) as db:
        await configure_connection(db)
        await create_all_tables(db)
        await db.commit()

    service = HistoryService(temp_db)
    record = await service.create_record(
        HistoryRecordCreate(
            task_name="log throttle",
            folder_path="/incoming/a.mkv",
            status=TaskStatus.RUNNING,
            total_files=1,
            success_count=0,
            failed_count=0,
            duration_seconds=0,
        )
    )
    original_persist = service._persist_scrape_logs
    persist = AsyncMock(wraps=original_persist)
    monkeypatch.setattr(service, "_persist_scrape_logs", persist)
    timestamps = iter((0.0, 0.1, 0.6))
    monkeypatch.setattr(history_module, "monotonic", lambda: next(timestamps))

    await service.update_scrape_logs(record.id, [ScrapeLogStep(name="first")])
    await service.update_scrape_logs(record.id, [ScrapeLogStep(name="second")])
    assert persist.await_count == 1

    await service.update_scrape_logs(record.id, [ScrapeLogStep(name="latest")])
    assert persist.await_count == 2

    await service.flush_and_clear_log_cache(record.id)
    assert persist.await_count == 3
    saved = await service.get_record(record.id)
    assert saved is not None
    assert [step.name for step in saved.scrape_logs] == ["latest"]
