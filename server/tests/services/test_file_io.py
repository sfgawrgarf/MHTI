"""Cloud-only regression tests for bounded and cancellation-safe filesystem I/O."""

import asyncio
from threading import Event, get_ident
from unittest.mock import Mock

import pytest

from server.models.organize import OrganizeMode
from server.services.file_io import FileIOExecutor, check_file_cancelled
from server.services.file_operations import publish_file
from server.services.file_service import FileService
from server.services.task_limiter import TaskLimiter


async def reached(event: Event) -> None:
    async with asyncio.timeout(3):
        while not event.is_set():
            await asyncio.sleep(0.001)


@pytest.mark.asyncio
async def test_io_runs_off_loop_and_bounds_submissions():
    executor = FileIOExecutor(workers=1)
    started, release = Event(), Event()
    loop_thread = get_ident()

    def blocking():
        assert get_ident() != loop_thread
        started.set()
        assert release.wait(3)
        return "done"

    second = Mock(return_value="second")
    first_task = asyncio.create_task(executor.run(blocking))
    try:
        await reached(started)
        second_task = asyncio.create_task(executor.run(second))
        await asyncio.sleep(0)
        assert executor.snapshot() == {"workers": 1, "active": 1, "waiting": 1}
        second.assert_not_called()
        second_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await second_task
        second.assert_not_called()
        release.set()
        assert await first_task == "done"
    finally:
        release.set()
        await asyncio.gather(first_task, return_exceptions=True)
        executor.close()


@pytest.mark.asyncio
async def test_cancel_drains_thread_and_holds_slot_even_on_second_cancel():
    executor = FileIOExecutor(workers=1)
    started, release, exited = Event(), Event(), Event()

    def blocking():
        started.set()
        try:
            assert release.wait(3)
            check_file_cancelled()
        finally:
            exited.set()

    task = asyncio.create_task(executor.run(blocking))
    second = Mock()
    next_task = None
    try:
        await reached(started)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        next_task = asyncio.create_task(executor.run(second))
        await asyncio.sleep(0)
        second.assert_not_called()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert exited.is_set()
        await next_task
        second.assert_called_once()
    finally:
        release.set()
        await asyncio.gather(task, *([next_task] if next_task else []), return_exceptions=True)
        executor.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [OrganizeMode.COPY, OrganizeMode.MOVE])
async def test_cancel_before_commit_keeps_source_and_old_destination(tmp_path, monkeypatch, mode):
    from server.services import file_operations

    source, destination = tmp_path / "source.mkv", tmp_path / "destination.mkv"
    source.write_bytes(b"new media")
    destination.write_bytes(b"old media")
    copied, release = Event(), Event()
    original_copy = file_operations.copy_file

    def paused_copy(*args):
        original_copy(*args)
        copied.set()
        assert release.wait(3)

    monkeypatch.setattr(file_operations, "copy_file", paused_copy)
    executor = FileIOExecutor(workers=1)
    task = asyncio.create_task(executor.run(publish_file, source, destination, mode, overwrite=True))
    try:
        await reached(copied)
        task.cancel()
        await asyncio.sleep(0)
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert source.read_bytes() == b"new media"
        assert destination.read_bytes() == b"old media"
        assert not list(tmp_path.glob(".mhti-transfer-*"))
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        executor.close()


@pytest.mark.asyncio
async def test_local_scan_uses_off_loop_path(monkeypatch, tmp_path):
    service = FileService()
    loop_thread = get_ident()

    def scan(path, locator=None):
        assert get_ident() != loop_thread
        return []

    monkeypatch.setattr(service, "scan_folder", scan)
    assert await service.scan_folder_async(str(tmp_path)) == []


@pytest.mark.asyncio
async def test_resize_retains_active_accounting():
    limiter = TaskLimiter(2)
    await limiter.__aenter__()
    await limiter.__aenter__()
    await limiter.resize(1)
    entered = asyncio.Event()

    async def waiting():
        async with limiter:
            entered.set()

    task = asyncio.create_task(waiting())
    await asyncio.sleep(0)
    assert not entered.is_set()
    await limiter.__aexit__()
    await asyncio.sleep(0)
    assert not entered.is_set()
    await limiter.__aexit__()
    await asyncio.wait_for(task, 1)
    assert entered.is_set()
    assert limiter.active == 0


@pytest.mark.asyncio
async def test_cancelled_waiter_does_not_leak_capacity():
    limiter = TaskLimiter(1)
    await limiter.__aenter__()
    waiting = asyncio.create_task(limiter.__aenter__())
    await asyncio.sleep(0)
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    assert limiter.active == 1
    await limiter.__aexit__()
    async with limiter:
        assert limiter.active == 1
    assert limiter.active == 0


@pytest.mark.asyncio
async def test_timeout_is_reported_only_after_thread_cleanup():
    executor = FileIOExecutor(workers=1)
    entered, release, finished = Event(), Event(), Event()

    def operation():
        entered.set()
        try:
            assert release.wait(3)
            check_file_cancelled()
        finally:
            finished.set()

    task = asyncio.create_task(asyncio.wait_for(executor.run(operation), 0.03))
    try:
        await reached(entered)
        await asyncio.sleep(0.06)
        assert not task.done()
        release.set()
        with pytest.raises(TimeoutError):
            await task
        assert finished.is_set()
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        executor.close()


def test_same_filesystem_move_does_not_copy_payload(tmp_path, monkeypatch):
    source, destination = tmp_path / "source", tmp_path / "destination"
    source.write_bytes(b"media")
    copy = Mock(side_effect=AssertionError("same-filesystem move must not copy"))
    monkeypatch.setattr("server.services.file_operations.copy_file", copy)
    publish_file(source, destination, OrganizeMode.MOVE)
    assert destination.read_bytes() == b"media"
    assert not source.exists()
    copy.assert_not_called()
