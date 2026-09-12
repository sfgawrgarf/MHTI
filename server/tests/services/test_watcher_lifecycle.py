"""Watcher lifecycle and background-error regressions."""

import asyncio
import logging
from unittest.mock import AsyncMock

import pytest

from server.models.watcher import WatchedFolder, WatcherStatus
from server.services import watcher_service as watcher_module
from server.services.watcher_service import WatcherService


class FakeStrategy:
    def __init__(self, folder: WatchedFolder) -> None:
        self.folder = folder
        self.stopped = False

    async def stop(self) -> None:
        self.stopped = True


class FailingStopStrategy(FakeStrategy):
    async def stop(self) -> None:
        raise RuntimeError("strategy stop failed")


@pytest.mark.asyncio
async def test_failed_start_rolls_back_partial_watcher_state(
    temp_db, monkeypatch
) -> None:
    service = WatcherService(temp_db)
    first = WatchedFolder(id="first", path="/first")
    second = WatchedFolder(id="second", path="/second")
    strategy = FakeStrategy(first)
    monkeypatch.setattr(
        service,
        "list_folders",
        AsyncMock(return_value=([first, second], 2)),
    )

    async def start_folder(folder: WatchedFolder) -> None:
        if folder.id == first.id:
            service._strategies[folder.id] = strategy
            return
        raise RuntimeError("strategy startup failed")

    monkeypatch.setattr(service, "_start_folder_watch", start_folder)

    with pytest.raises(RuntimeError, match="strategy startup failed"):
        await service.start()

    assert strategy.stopped is True
    assert service._running is False
    assert service._status == WatcherStatus.STOPPED
    assert service._strategies == {}
    assert service._process_task is None
    assert service._initial_scan_task is None


@pytest.mark.asyncio
async def test_concurrent_start_is_idempotent_and_stop_clears_tasks(
    temp_db, monkeypatch
) -> None:
    service = WatcherService(temp_db)
    list_folders = AsyncMock(return_value=([], 0))
    monkeypatch.setattr(service, "list_folders", list_folders)

    initial_scan_started = asyncio.Event()

    async def initial_scan(_folders) -> None:
        initial_scan_started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(service, "_initial_scan", initial_scan)

    await asyncio.gather(service.start(), service.start())
    await initial_scan_started.wait()
    process_task = service._process_task
    initial_task = service._initial_scan_task

    assert list_folders.await_count == 1
    assert service._running is True
    assert service._status == WatcherStatus.RUNNING
    assert process_task is not None
    assert initial_task is not None

    await service.stop()

    assert process_task.done()
    assert initial_task.cancelled()
    assert service._process_task is None
    assert service._initial_scan_task is None
    assert service._running is False
    assert service._status == WatcherStatus.STOPPED


@pytest.mark.asyncio
async def test_background_task_failure_is_logged(caplog) -> None:
    async def fail() -> None:
        raise RuntimeError("background failed")

    with caplog.at_level(logging.ERROR):
        task = watcher_module._create_background_task(fail(), "watcher-test-task")
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.sleep(0)

    assert "watcher-test-task stopped unexpectedly" in caplog.text
    assert "background failed" in caplog.text


@pytest.mark.asyncio
async def test_failed_strategy_stop_remains_visible_for_retry(temp_db, caplog) -> None:
    service = WatcherService(temp_db)
    folder = WatchedFolder(id="stuck", path="/stuck")
    strategy = FailingStopStrategy(folder)
    service._running = True
    service._status = WatcherStatus.RUNNING
    service._strategies[folder.id] = strategy

    with caplog.at_level(logging.ERROR):
        await service.stop()

    assert service._running is False
    assert service._status == WatcherStatus.ERROR
    assert service._strategies == {folder.id: strategy}
    assert "strategy stop failed" in caplog.text

    with pytest.raises(RuntimeError, match="拒绝重复启动"):
        await service.start()
