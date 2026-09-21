"""Scheduled task execution and lifecycle regressions."""

import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

from pydantic import ValidationError
import pytest

from server.core.db.connection import db_connection
from server.models.scheduler import ScheduledTask, ScheduledTaskCreate, ScheduledTaskUpdate
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


@pytest.mark.parametrize("field", ["name", "folder_path", "cron_expression"])
def test_create_task_rejects_blank_required_fields(field: str) -> None:
    values = {
        "name": "nightly",
        "folder_path": "/media/tv",
        "cron_expression": "0 2 * * *",
    }
    values[field] = "   "

    with pytest.raises(ValidationError):
        ScheduledTaskCreate(**values)


@pytest.mark.asyncio
async def test_p115_folder_is_rejected_but_similar_local_prefix_is_allowed(temp_db) -> None:
    service = SchedulerService(temp_db)

    with pytest.raises(ValueError, match="115"):
        await service.create_task(
            ScheduledTaskCreate(
                name="cloud",
                folder_path="/115网盘/电视剧",
                cron_expression="0 2 * * *",
            )
        )

    task = await service.create_task(
        ScheduledTaskCreate(
            name="local backup",
            folder_path="/115网盘备份",
            cron_expression="0 2 * * *",
        )
    )
    assert task.folder_path == "/115网盘备份"

    with pytest.raises(ValueError, match="115"):
        await service.update_task(
            task.id,
            ScheduledTaskUpdate(folder_path="/115网盘/电影"),
        )


@pytest.mark.asyncio
async def test_legacy_invalid_tasks_are_disabled_during_initialization(temp_db) -> None:
    seed = SchedulerService(temp_db)
    await seed._ensure_db()
    now = datetime.now().isoformat()
    async with db_connection(temp_db) as db:
        await db.executemany(
            """INSERT INTO scheduled_tasks
               (id, name, folder_path, cron_expression, enabled, next_run, created_at)
               VALUES (?, ?, ?, ?, 1, NULL, ?)""",
            [
                ("bad-cron", "bad cron", "/media/tv", "not-a-cron", now),
                ("blank-path", "blank", "   ", "0 2 * * *", now),
                ("cloud-path", "cloud", "/115网盘/电视剧", "0 2 * * *", now),
            ],
        )
        await db.commit()

    service = SchedulerService(temp_db)
    tasks = await service.list_tasks()

    assert {task.id for task in tasks if not task.enabled} == {
        "bad-cron",
        "blank-path",
        "cloud-path",
    }


@pytest.mark.asyncio
async def test_executor_defensively_rejects_blank_legacy_path(temp_db) -> None:
    executor = AsyncMock()
    service = SchedulerService(temp_db, executor=executor)
    task = ScheduledTask(
        id="legacy",
        name="legacy",
        folder_path=" ",
        cron_expression="0 2 * * *",
    )

    with pytest.raises(ValueError, match="不能为空"):
        await service._execute_task(task)
    executor.assert_not_awaited()


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


@pytest.mark.asyncio
async def test_failed_execution_retries_then_clears_error_on_success(temp_db) -> None:
    executor = AsyncMock(side_effect=[RuntimeError("temporary"), None])
    service = SchedulerService(temp_db, executor=executor, retry_delays=(0,))
    task = await service.create_task(
        ScheduledTaskCreate(
            name="retry",
            folder_path="/media/tv",
            cron_expression="* * * * *",
        )
    )
    due = datetime.now() - timedelta(minutes=1)
    async with db_connection(temp_db) as db:
        await db.execute(
            "UPDATE scheduled_tasks SET next_run = ? WHERE id = ?",
            (due.isoformat(), task.id),
        )
        await db.commit()

    await service.run_due_tasks(datetime.now())
    retrying = await service.get_task(task.id)
    assert retrying is not None
    assert retrying.last_status == "retrying"
    assert retrying.last_error == "temporary"
    assert retrying.retry_count == 1

    await service.run_due_tasks(datetime.now() + timedelta(seconds=1))
    succeeded = await service.get_task(task.id)
    assert succeeded is not None
    assert succeeded.last_status == "success"
    assert succeeded.last_error is None
    assert succeeded.retry_count == 0
    assert succeeded.last_attempt is not None
    assert executor.await_count == 2


@pytest.mark.asyncio
async def test_retry_policy_is_bounded_and_returns_to_cron_schedule(temp_db) -> None:
    executor = AsyncMock(side_effect=RuntimeError("persistent"))
    service = SchedulerService(temp_db, executor=executor, retry_delays=(0, 0))
    task = await service.create_task(
        ScheduledTaskCreate(
            name="bounded",
            folder_path="/media/tv",
            cron_expression="* * * * *",
        )
    )
    async with db_connection(temp_db) as db:
        await db.execute(
            "UPDATE scheduled_tasks SET next_run = ? WHERE id = ?",
            ((datetime.now() - timedelta(minutes=1)).isoformat(), task.id),
        )
        await db.commit()

    for _ in range(3):
        await service.run_due_tasks(datetime.now() + timedelta(seconds=1))

    failed = await service.get_task(task.id)
    assert failed is not None
    assert failed.last_status == "failed"
    assert failed.retry_count == 3
    assert failed.last_error == "persistent"
    assert failed.next_run is not None
    assert executor.await_count == 3


@pytest.mark.asyncio
async def test_running_task_is_recovered_after_restart(temp_db) -> None:
    seed = SchedulerService(temp_db)
    task = await seed.create_task(
        ScheduledTaskCreate(
            name="interrupted",
            folder_path="/media/tv",
            cron_expression="* * * * *",
        )
    )
    async with db_connection(temp_db) as db:
        await db.execute(
            """UPDATE scheduled_tasks
               SET last_status = 'running', next_run = NULL, retry_count = 0
               WHERE id = ?""",
            (task.id,),
        )
        await db.commit()

    restarted = SchedulerService(temp_db, retry_delays=(30,))
    recovered = await restarted.get_task(task.id)

    assert recovered is not None
    assert recovered.last_status == "retrying"
    assert recovered.retry_count == 1
    assert recovered.last_error == "上次执行因服务重启而中断"
    assert recovered.next_run is not None


@pytest.mark.asyncio
async def test_disabled_running_task_is_not_requeued_after_restart(temp_db) -> None:
    seed = SchedulerService(temp_db)
    task = await seed.create_task(
        ScheduledTaskCreate(
            name="disabled interrupted",
            folder_path="/media/tv",
            cron_expression="* * * * *",
        )
    )
    async with db_connection(temp_db) as db:
        await db.execute(
            """UPDATE scheduled_tasks
               SET enabled = 0, last_status = 'running', next_run = NULL
               WHERE id = ?""",
            (task.id,),
        )
        await db.commit()

    recovered = await SchedulerService(temp_db).get_task(task.id)

    assert recovered is not None
    assert recovered.enabled is False
    assert recovered.last_status == "failed"
    assert recovered.last_error == "上次执行因服务重启而中断"
    assert recovered.next_run is None


@pytest.mark.asyncio
async def test_completion_uses_cron_updated_while_task_was_running(temp_db) -> None:
    service = SchedulerService(temp_db)
    task = await service.create_task(
        ScheduledTaskCreate(
            name="rescheduled",
            folder_path="/media/tv",
            cron_expression="* * * * *",
        )
    )

    async def update_schedule(_task: ScheduledTask) -> None:
        await service.update_task(
            task.id,
            ScheduledTaskUpdate(cron_expression="0 0 * * *"),
        )

    service._executor = update_schedule
    async with db_connection(temp_db) as db:
        await db.execute(
            "UPDATE scheduled_tasks SET next_run = ? WHERE id = ?",
            ((datetime.now() - timedelta(minutes=1)).isoformat(), task.id),
        )
        await db.commit()

    await service.run_due_tasks(datetime.now())
    updated = await service.get_task(task.id)

    assert updated is not None
    assert updated.cron_expression == "0 0 * * *"
    assert updated.last_run is not None
    assert updated.next_run == service._calculate_next_run(
        updated.cron_expression, updated.last_run
    )


@pytest.mark.asyncio
async def test_completion_does_not_reschedule_task_disabled_while_running(
    temp_db,
) -> None:
    service = SchedulerService(temp_db)
    task = await service.create_task(
        ScheduledTaskCreate(
            name="disabled during run",
            folder_path="/media/tv",
            cron_expression="* * * * *",
        )
    )

    async def disable_task(_task: ScheduledTask) -> None:
        await service.update_task(task.id, ScheduledTaskUpdate(enabled=False))

    service._executor = disable_task
    async with db_connection(temp_db) as db:
        await db.execute(
            "UPDATE scheduled_tasks SET next_run = ? WHERE id = ?",
            ((datetime.now() - timedelta(minutes=1)).isoformat(), task.id),
        )
        await db.commit()

    await service.run_due_tasks(datetime.now())
    updated = await service.get_task(task.id)

    assert updated is not None
    assert updated.enabled is False
    assert updated.last_status == "success"
    assert updated.next_run is None


@pytest.mark.asyncio
async def test_failure_does_not_retry_task_disabled_while_running(temp_db) -> None:
    service = SchedulerService(temp_db, retry_delays=(0, 0))
    task = await service.create_task(
        ScheduledTaskCreate(
            name="disabled failure",
            folder_path="/media/tv",
            cron_expression="* * * * *",
        )
    )

    async def disable_then_fail(_task: ScheduledTask) -> None:
        await service.update_task(task.id, ScheduledTaskUpdate(enabled=False))
        raise RuntimeError("failed after disable")

    service._executor = disable_then_fail
    async with db_connection(temp_db) as db:
        await db.execute(
            "UPDATE scheduled_tasks SET next_run = ? WHERE id = ?",
            ((datetime.now() - timedelta(minutes=1)).isoformat(), task.id),
        )
        await db.commit()

    await service.run_due_tasks(datetime.now())
    updated = await service.get_task(task.id)

    assert updated is not None
    assert updated.enabled is False
    assert updated.last_status == "failed"
    assert updated.last_error == "failed after disable"
    assert updated.next_run is None
