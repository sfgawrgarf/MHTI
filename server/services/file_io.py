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
    event = _cancel_event.get()
    if event is not None and event.is_set():
        raise FileIOCancelled("文件操作已取消")


class FileIOExecutor:
    """Do not release capacity or report cancellation while a thread still writes."""

    def __init__(self, workers: int = 2):
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="mhti-file")
        self.slots = asyncio.Semaphore(workers)

    async def run(self, function, /, *args, **kwargs):
        async with self.slots:
            event = Event()
            context = copy_context()
            context.run(_cancel_event.set, event)

            def invoke():
                check_file_cancelled()
                return function(*args, **kwargs)

            future = asyncio.get_running_loop().run_in_executor(self.pool, context.run, invoke)
            try:
                return await asyncio.shield(future)
            except asyncio.CancelledError:
                event.set()
                # Cancellation cannot kill a thread. Drain it even if shutdown
                # cancels this task again; cooperative copies/scans stop promptly.
                while not future.done():
                    try:
                        await asyncio.shield(future)
                    except asyncio.CancelledError:
                        continue
                    except Exception:
                        break
                if not future.cancelled():
                    future.exception()  # retrieve any cooperative cancellation/error
                raise

    def close(self) -> None:
        # All callers are drained before application shutdown reaches this point.
        self.pool.shutdown(wait=False, cancel_futures=True)


_executors: WeakKeyDictionary = WeakKeyDictionary()


async def run_file_io(function, /, *args, **kwargs):
    loop = asyncio.get_running_loop()
    executor = _executors.get(loop)
    if executor is None:
        executor = FileIOExecutor()
        _executors[loop] = executor
    return await executor.run(partial(function, *args, **kwargs))


def shutdown_file_io() -> None:
    executor = _executors.pop(asyncio.get_running_loop(), None)
    if executor is not None:
        executor.close()
