"""Loop-scoped HTTP connection reuse, adapted from upstream v2.0.1.

See licenses/MHTI-upstream/. Authentication stays on individual requests;
proxy/timeout changes select another client and never inherit request headers.
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from weakref import WeakKeyDictionary

import httpx

logger = logging.getLogger(__name__)
_pools: WeakKeyDictionary = WeakKeyDictionary()
_locks: WeakKeyDictionary = WeakKeyDictionary()


@asynccontextmanager
async def shared_http_client(*, proxy: str | None = None, timeout: float = 30.0):
    loop = asyncio.get_running_loop()
    lock = _locks.setdefault(loop, asyncio.Lock())
    key = (proxy or "", float(timeout))
    async with lock:
        pool = _pools.setdefault(loop, {})
        entry = pool.get(key)
        if entry is None or entry[0].is_closed is True:
            context = httpx.AsyncClient(proxy=proxy, timeout=timeout)
            client = await context.__aenter__()
            entry = pool[key] = (context, client)
    yield entry[1]


async def close_http_clients() -> None:
    loop = asyncio.get_running_loop()
    async with _locks.setdefault(loop, asyncio.Lock()):
        pool = _pools.pop(loop, {})
        for context, _client in pool.values():
            try:
                await context.__aexit__(None, None, None)
            except Exception:
                # Keys contain proxy credentials; never write them to logs.
                logger.exception("关闭共享 HTTP 客户端失败")
