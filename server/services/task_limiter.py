"""Resize concurrency without replacing the accounting for active tasks."""

import asyncio


class TaskLimiter:
    def __init__(self, limit: int):
        self.limit = limit
        self.active = 0
        self.condition = asyncio.Condition()

    async def resize(self, limit: int) -> None:
        async with self.condition:
            self.limit = max(1, limit)
            self.condition.notify_all()

    async def __aenter__(self):
        async with self.condition:
            await self.condition.wait_for(lambda: self.active < self.limit)
            self.active += 1
        return self

    async def __aexit__(self, *exc):
        async with self.condition:
            self.active -= 1
            self.condition.notify_all()
