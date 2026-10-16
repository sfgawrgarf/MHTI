"""Unit tests for SchedulerService（定时任务 CRUD 与 cron 计算）。"""

from pathlib import Path

import aiosqlite
import pytest

from server.application.scheduler_service import SchedulerService
from server.infrastructure.db import create_all_tables
from server.models.scheduler import ScheduledTaskCreate, ScheduledTaskUpdate


async def _initialize_test_db(db_path: Path) -> None:
    """Create the test schema in a temporary database."""
    async with aiosqlite.connect(db_path) as db:
        await create_all_tables(db)
        await db.commit()


@pytest.mark.asyncio
async def test_create_task_computes_next_run(temp_db: Path) -> None:
    """启用任务应计算 next_run；未启用任务 next_run 为 None。"""
    await _initialize_test_db(temp_db)
    service = SchedulerService(db_path=temp_db)

    enabled = await service.create_task(
        ScheduledTaskCreate(
            name="每日扫描",
            folder_path="/media/tv",
            cron_expression="0 3 * * *",
            enabled=True,
        )
    )
    assert enabled.name == "每日扫描"
    assert enabled.enabled is True
    assert enabled.next_run is not None

    disabled = await service.create_task(
        ScheduledTaskCreate(
            name="暂停任务",
            folder_path="/media/movies",
            cron_expression="0 4 * * *",
            enabled=False,
        )
    )
    assert disabled.enabled is False
    assert disabled.next_run is None


@pytest.mark.asyncio
async def test_create_task_with_invalid_cron_is_rejected(temp_db: Path) -> None:
    await _initialize_test_db(temp_db)
    service = SchedulerService(db_path=temp_db)
    with pytest.raises(ValueError, match="Cron"):
        await service.create_task(ScheduledTaskCreate(
            name="坏表达式", folder_path="/media/tv", cron_expression="not a cron",
        ))
    assert await service.list_tasks() == []


@pytest.mark.asyncio
async def test_get_task_roundtrip_and_missing(temp_db: Path) -> None:
    """创建后可读回全部字段；不存在的 id 返回 None。"""
    await _initialize_test_db(temp_db)
    service = SchedulerService(db_path=temp_db)

    created = await service.create_task(
        ScheduledTaskCreate(
            name="每日扫描",
            folder_path="/media/tv",
            cron_expression="30 2 * * *",
        )
    )

    fetched = await service.get_task(created.id)
    assert fetched is not None
    assert fetched.id == created.id
    assert fetched.folder_path == "/media/tv"
    assert fetched.cron_expression == "30 2 * * *"
    assert fetched.created_at is not None

    assert await service.get_task("no-such-id") is None


@pytest.mark.asyncio
async def test_list_tasks_returns_all(temp_db: Path) -> None:
    """list 应返回全部任务。"""
    await _initialize_test_db(temp_db)
    service = SchedulerService(db_path=temp_db)

    first = await service.create_task(
        ScheduledTaskCreate(name="A", folder_path="/a", cron_expression="0 1 * * *")
    )
    second = await service.create_task(
        ScheduledTaskCreate(name="B", folder_path="/b", cron_expression="0 2 * * *")
    )

    tasks = await service.list_tasks()
    ids = {t.id for t in tasks}
    assert ids == {first.id, second.id}


@pytest.mark.asyncio
async def test_update_task_recalculates_next_run(temp_db: Path) -> None:
    """更新字段后应持久化并重算 next_run。"""
    await _initialize_test_db(temp_db)
    service = SchedulerService(db_path=temp_db)

    created = await service.create_task(
        ScheduledTaskCreate(name="旧名", folder_path="/a", cron_expression="0 1 * * *")
    )

    updated = await service.update_task(
        created.id,
        ScheduledTaskUpdate(name="新名", cron_expression="0 5 * * *"),
    )
    assert updated is not None
    assert updated.name == "新名"
    assert updated.cron_expression == "0 5 * * *"
    assert updated.next_run is not None

    refetched = await service.get_task(created.id)
    assert refetched is not None
    assert refetched.name == "新名"

    # 停用后 next_run 应清空
    disabled = await service.update_task(created.id, ScheduledTaskUpdate(enabled=False))
    assert disabled is not None
    assert disabled.enabled is False
    assert disabled.next_run is None

    assert await service.update_task("no-such-id", ScheduledTaskUpdate(name="x")) is None


@pytest.mark.asyncio
async def test_toggle_task_switches_enabled(temp_db: Path) -> None:
    """toggle 应在启用/停用之间切换。"""
    await _initialize_test_db(temp_db)
    service = SchedulerService(db_path=temp_db)

    created = await service.create_task(
        ScheduledTaskCreate(name="T", folder_path="/t", cron_expression="0 6 * * *")
    )

    toggled_off = await service.toggle_task(created.id)
    assert toggled_off is not None
    assert toggled_off.enabled is False
    assert toggled_off.next_run is None

    toggled_on = await service.toggle_task(created.id)
    assert toggled_on is not None
    assert toggled_on.enabled is True
    assert toggled_on.next_run is not None

    assert await service.toggle_task("no-such-id") is None


@pytest.mark.asyncio
async def test_delete_task(temp_db: Path) -> None:
    """删除存在的任务返回 True，重复删除返回 False。"""
    await _initialize_test_db(temp_db)
    service = SchedulerService(db_path=temp_db)

    created = await service.create_task(
        ScheduledTaskCreate(name="T", folder_path="/t", cron_expression="0 7 * * *")
    )

    assert await service.delete_task(created.id) is True
    assert await service.get_task(created.id) is None
    assert await service.delete_task(created.id) is False
