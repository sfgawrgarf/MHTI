"""Filesystem and provider boundaries found by the complete main audit."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from server.domain.artifacts.subtitle_service import SubtitleService
from server.domain.integration.p115_service import P115Service
from server.domain.media.file_service import FileService
from server.application.scraping.p115_storage_provider import P115StorageProvider
from server.application.watcher_service import P115EventStrategy
from server.models.storage import StorageLocator, StorageProvider
from server.models.watcher import WatchedFolder, WatcherMode
from server.tests.domain.test_p115_service import build_fake_p115_module


def test_browse_survives_a_broken_symlink(tmp_path):
    (tmp_path / 'good.mkv').write_bytes(b'good')
    (tmp_path / 'broken.mkv').symlink_to(tmp_path / 'missing.mkv')
    _, _, entries, _, _, _ = FileService()._browse_local_path(str(tmp_path), 1, 20)
    assert any(entry.name == 'good.mkv' for entry in entries)


def test_concurrent_subtitle_rename_preserves_both_contents(tmp_path, monkeypatch):
    import server.domain.artifacts.subtitle_service as module

    first, second = tmp_path / 'first.srt', tmp_path / 'second.srt'
    first.write_bytes(b'first subtitle')
    second.write_bytes(b'second subtitle')
    barrier = Barrier(2)
    original = module.shutil.move

    def concurrent_move(*args, **kwargs):
        barrier.wait(timeout=5)
        return original(*args, **kwargs)

    monkeypatch.setattr(module.shutil, 'move', concurrent_move)
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = [pool.submit(SubtitleService().rename_subtitle, str(path), 'same', False)
                   for path in (first, second)]
        results = [future.result(timeout=10) for future in pending]
    assert sum(result.success for result in results) == 1
    assert {path.read_bytes() for path in tmp_path.glob('*.srt')} == {
        b'first subtitle', b'second subtitle',
    }


@pytest.mark.asyncio
async def test_logout_invalidates_an_inflight_115_qr_completion(config_service, monkeypatch):
    fake_module, fake_const = build_fake_p115_module()

    async def load():
        return fake_module, fake_const

    monkeypatch.setattr('server.domain.integration.p115_service._load_p115client', load)
    service = P115Service(config_service)
    session = await service.start_qr_login('alipaymini')
    original = service._call_login_api
    entered, release = asyncio.Event(), asyncio.Event()

    async def delayed(method, *args, **kwargs):
        result = await original(method, *args, **kwargs)
        if method.__name__ == 'login_qrcode_scan_result':
            entered.set()
            await release.wait()
        return result

    monkeypatch.setattr(service, '_call_login_api', delayed)
    pending = asyncio.create_task(service.poll_qr_login(session.uid, session.app))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        await service.clear_login_state()
        release.set()
        await pending
        saved = await config_service.get_115_config()
        assert not saved.is_logged_in, 'logout was undone by a stale QR response'
        assert not saved.cookies
    finally:
        release.set()
        await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.asyncio
async def test_115_events_with_equal_timestamp_are_not_lost(monkeypatch):
    seen = []
    folder = WatchedFolder(id='cloud', path='/115网盘/Inbox', provider='115',
                           file_id='10', mode=WatcherMode.EVENT)
    strategy = P115EventStrategy(folder, lambda path, _: seen.append(path))
    strategy._directory_paths = {'10': folder.path}
    strategy._watched_dir_ids = {'10'}
    strategy._last_update_time = 99
    strategy._running = True

    def event(file_id):
        return {'update_time': 100, 'behavior_type': 'receive_files', 'items': [
            {'file_id': file_id, 'parent_id': '10', 'file_name': f'{file_id}.mkv', 'ico': 'mkv'},
        ]}

    client = SimpleNamespace(life_list=AsyncMock(side_effect=[
        {'state': True, 'data': {'list': [event('a')]}},
        {'state': True, 'data': {'list': [event('a'), event('b')]}},
    ]))
    monkeypatch.setattr(strategy, '_get_client', AsyncMock(return_value=client))
    monkeypatch.setattr(strategy, '_collect_dir_ids', AsyncMock())
    iterations = 0

    async def advance(_):
        nonlocal iterations
        iterations += 1
        if iterations > 2:
            strategy._running = False

    monkeypatch.setattr('server.application.watcher_service.asyncio.sleep', advance)
    await strategy._event_loop()
    assert seen == ['/115网盘/Inbox/a.mkv', '/115网盘/Inbox/b.mkv']


@pytest.mark.asyncio
async def test_unresolved_115_copy_never_renames_the_source(config_service, monkeypatch):
    client = SimpleNamespace(fs_copy=AsyncMock(return_value={'state': True, 'data': {'file_id': 'source'}}),
                             fs_rename=AsyncMock(return_value={'state': True}))
    provider = P115StorageProvider(config_service)
    monkeypatch.setattr(provider, '_get_client', AsyncMock(return_value=(client, 'alipaymini')))
    monkeypatch.setattr(provider, '_find_file_ids_in_dir', AsyncMock(return_value=set()))
    source = StorageLocator(provider=StorageProvider.P115, path='/115网盘/original.mkv', file_id='source')
    try:
        await provider.copy(source, 'renamed.mkv', 'target')
    except ValueError:
        pass
    client.fs_rename.assert_not_awaited()
