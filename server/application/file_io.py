"""Bounded, cancellation-aware offloading for local filesystem operations."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar, copy_context
from functools import partial
from threading import Event
from weakref import WeakKeyDictionary


class FileIOCancelled(Exception):
    """A synchronous operation stopped at a safe cancellation checkpoint."""


_cancel_event: ContextVar[Event | None] = ContextVar("file_io_cancel", default=None)


def check_file_cancelled() -> None:
    """Raise when the caller requested cancellation before a write starts."""
    event = _cancel_event.get()
    if event is not None and event.is_set():
        raise FileIOCancelled("文件操作已取消")


class FileIOExecutor:
    """Bound local I/O and drain worker threads before reporting cancellation."""

    def __init__(self, workers: int = 2) -> None:
        self.workers = workers
        self.pool = ThreadPoolExecutor(
            max_workers=workers,
            thread_name_prefix="mhti-file",
        )
        self.slots = asyncio.Semaphore(workers)
        self.active = 0
        self.waiting = 0

    async def run(self, function, /, *args, **kwargs):
        self.waiting += 1
        entered = False
        try:
            await self.slots.acquire()
            entered = True
        finally:
            self.waiting -= 1

        self.active += 1
        try:
            event = Event()
            context = copy_context()
            context.run(_cancel_event.set, event)

            def invoke():
                check_file_cancelled()
                return function(*args, **kwargs)

            future = asyncio.get_running_loop().run_in_executor(
                self.pool, context.run, invoke
            )
            try:
                return await asyncio.shield(future)
            except asyncio.CancelledError:
                event.set()
                while not future.done():
                    try:
                        await asyncio.shield(future)
                    except asyncio.CancelledError:
                        continue
                    except Exception:
                        break
                if not future.cancelled():
                    future.exception()
                raise
        finally:
            self.active -= 1
            if entered:
                self.slots.release()

    def snapshot(self) -> dict[str, int]:
        return {
            "workers": self.workers,
            "active": self.active,
            "waiting": self.waiting,
        }

    def close(self) -> None:
        self.pool.shutdown(wait=False, cancel_futures=True)


_executors: WeakKeyDictionary = WeakKeyDictionary()


async def run_file_io(function, /, *args, **kwargs):
    """Run a blocking local file operation on the current event loop's executor."""
    loop = asyncio.get_running_loop()
    executor = _executors.get(loop)
    if executor is None:
        executor = FileIOExecutor()
        _executors[loop] = executor
    return await executor.run(partial(function, *args, **kwargs))


def get_file_io_snapshot() -> dict[str, int]:
    """Return the current loop's executor utilization."""
    executor = _executors.get(asyncio.get_running_loop())
    if executor is None:
        return {"workers": 2, "active": 0, "waiting": 0}
    return executor.snapshot()


def shutdown_file_io() -> None:
    """Close the current loop's executor after callers have drained."""
    executor = _executors.pop(asyncio.get_running_loop(), None)
    if executor is not None:
        executor.close()
