"""Reserve one local output name through metadata preparation and publication."""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from weakref import WeakKeyDictionary

from server.application.file_io import run_file_io

_states = WeakKeyDictionary()


@asynccontextmanager
async def reserve_destination(rename_service, request, *, metadata_dir=None, **options):
    loop = asyncio.get_running_loop()
    state = _states.setdefault(loop, {'lock': asyncio.Lock(), 'paths': set(), 'series': {}})
    path = None
    series_lock = None
    acquired = False
    try:
        async with state['lock']:
            path = Path(await run_file_io(rename_service.resolve_destination_path, request, **options))
            if path in state['paths']:
                if request.conflict_action != 'rename':
                    raise FileExistsError(str(path))
                original = path
                counter = 1
                while path in state['paths'] or path.exists() or path.is_symlink():
                    path = original.with_name(f'{original.stem} ({counter}){original.suffix}')
                    counter += 1
            if (path.exists() or path.is_symlink()) and path != Path(request.source_path) and request.conflict_action != 'overwrite':
                raise FileExistsError(str(path))
            state['paths'].add(path)
            key = str((Path(metadata_dir) / path.parent.parent.name) if metadata_dir else path.parent.parent)
            entry = state['series'].setdefault(key, [asyncio.Lock(), 0])
            entry[1] += 1
            series_lock = entry[0]
        await series_lock.acquire()
        acquired = True
        await run_file_io(path.parent.mkdir, parents=True, exist_ok=True)
        yield path
    finally:
        if acquired:
            series_lock.release()
        if path is not None and series_lock is not None:
            state['paths'].discard(path)
            entry[1] -= 1
            if entry[1] == 0:
                state['series'].pop(key, None)
