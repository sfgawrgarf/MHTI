"""Scheduled task execution and lifecycle regressions."""

import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from server.core.db.connection import db_connection
from server.models.scheduler import ScheduledTaskCreate, ScheduledTaskUpdate
from server.services.scheduler_service import SchedulerService


@pytest.mark.asyncio
async def test_invalid_cron_is_rejected_on_create_and_update(temp_db) -> None:
    service = SchedulerService(temp_db)

    with pytest.raises(ValueError, match="Cron"):
        await service.create_task(
            ScheduledTaskCreate(
                name="invalid",
                folder_path="/media/tv",
                cron_expression="not-a-cron",
            )
        )

    task = await service.create_task(
        ScheduledTaskCreate(
            name="valid",
            folder_path="/media/tv",
            cron_expression="*/5 * * * *",
        )
    )
    with pytest.raises(ValueError, match="Cron"):
        await service.update_task(
            task.id,
            ScheduledTaskUpdate(cron_expression="still-not-a-cron"),
        )


@pytest.mark.asyncio
async def test_due_task_executes_once_and_advances_schedule(temp_db) -> None:
    executor = AsyncMock()
    service = SchedulerService(temp_db, executor=executor)
    task = await service.create_task(
        ScheduledTaskCreate(
            name="nightly",
            folder_path="/media/tv",
            cron_expression="* * * * *",
        )
    )
    now = datetime.now().replace(microsecond=0)
    due_at = now - timedelta(minutes=1)
    async with db_connection(temp_db) as db:
        await db.execute(
            "UPDATE scheduled_tasks SET next_run = ? WHERE id = ?",
            (due_at.isoformat(), task.id),
        )
        await db.commit()

    claimed = await service.run_due_tasks(now)
    claimed_again = await service.run_due_tasks(now)
    updated = await service.get_task(task.id)

    assert claimed == 1
    assert claimed_again == 0
    executor.assert_awaited_once()
    assert executor.await_args.args[0].id == task.id
    assert updated is not None
    assert updated.last_run is not None
    assert updated.next_run is not None and updated.next_run > now


@pytest.mark.asyncio
async def test_scheduler_worker_start_and_stop_are_idempotent(temp_db) -> None:
    service = SchedulerService(temp_db, executor=AsyncMock(), poll_interval=60)

    await service.start()
    worker = service._worker_task
    await service.start()

    assert worker is not None
    assert service._worker_task is worker

    await service.stop()
    await service.stop()

    assert worker.done()
    assert service._worker_task is None


@pytest.mark.asyncio
async def test_scheduler_worker_recovers_after_polling_failure(
    temp_db, monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = SchedulerService(temp_db, executor=AsyncMock(), poll_interval=0.1)
    recovered = asyncio.Event()
    attempts = 0

    async def run_due_tasks() -> int:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("temporary database failure")
        recovered.set()
        return 0

    monkeypatch.setattr(service, "run_due_tasks", run_due_tasks)

    await service.start()
    await asyncio.wait_for(recovered.wait(), timeout=1)
    await service.stop()

    assert attempts >= 2
