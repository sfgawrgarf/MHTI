"""File ownership, publication and retry regressions from the post-104 review."""

from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from server.application.history_actions import HistoryScrapeActions
from server.application.scheduler_service import SchedulerService
from server.application.watcher_service import P115EventStrategy
from server.infrastructure.file_journal import signature
from server.infrastructure.log_handler import DatabaseLogHandler
from server.models.scheduler import ScheduledTaskCreate, ScheduledTaskUpdate
from server.models.scraper import ScrapeByIdRequest
from server.models.storage import StorageLocator
from server.models.watcher import WatchedFolder
from server.tests.application.test_audit_remediation import audit_env, record_for  # noqa: F401
from server.tests.application.test_scraper_service import FakeTMDB, _build_service, _patch_tail


@pytest.mark.asyncio
async def test_registration_and_retry_reject_replacement_at_original_path(audit_env):
    env = audit_env
    original = signature(env.source)
    target = env.output / "Show" / "Season 1" / "episode.mkv"
    target.parent.mkdir(parents=True)
    env.source.rename(target)
    env.source.write_bytes(b"later download")
    record = await record_for(env, f"{env.source} => {target}")
    await env.scraped.register_output(
        history_record_id=record.id, source_path=str(env.source), target_path=str(target),
        source_signature=original, tmdb_id=1, season=1, episode=1, title="Show",
    )
    assert not await env.scraped.can_delete_source(record.id, str(env.source))
    actions = HistoryScrapeActions(env.history, env.jobs, env.scraped, env.files)
    prepared, _, _ = await actions.prepare_input(record)
    assert prepared.staged_from == str(target)
    assert Path(prepared.path).read_bytes() == b"original video"
    await actions._release_input(prepared, dest_path=None, success=False)
    assert target.read_bytes() == b"original video"
    assert env.source.read_bytes() == b"later download"


@pytest.mark.asyncio
async def test_failed_retry_with_valid_source_restores_prior_library(audit_env):
    env = audit_env
    target = env.output / "Show" / "Season 1" / "ep.mkv"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"previous cut")
    nfo = env.metadata / "Show" / "Season 1" / "ep.nfo"
    nfo.parent.mkdir(parents=True)
    nfo.write_text("previous metadata")
    record = await record_for(env, f"{env.source} => {target}", register_target=True)
    actions = HistoryScrapeActions(env.history, env.jobs, env.scraped, env.files)
    prepared, _, _ = await actions.prepare_input(record)
    assert prepared.path == str(env.source)
    await actions._release_input(prepared, dest_path=None, success=False)
    assert target.read_bytes() == b"previous cut"
    assert nfo.read_text() == "previous metadata"


@pytest.mark.asyncio
async def test_overwritten_link_retains_metadata_layout(audit_env, monkeypatch):
    env = audit_env
    service = _build_service(env.db, FakeTMDB())
    _patch_tail(monkeypatch, service)
    args = dict(tmdb_id=123, season=1, episode=1, output_dir=str(env.output), metadata_dir=str(env.metadata))
    first = await service.scrape_by_id(ScrapeByIdRequest(file_path=str(env.source), link_mode="symlink", **args))
    assert Path(first.dest_path).is_symlink()
    incoming = env.source.with_name("replacement.mkv")
    incoming.write_bytes(b"new episode")
    second = await service.scrape_by_id(ScrapeByIdRequest(
        file_path=str(incoming), link_mode="copy", file_action="overwrite", **args))
    assert second.status.value == "success"
    assert first.nfo_path == second.nfo_path
    assert Path(second.nfo_path).stem == Path(second.dest_path).stem
    assert env.source.read_bytes() == b"original video"


@pytest.mark.asyncio
async def test_cloud_error_preserves_local_metadata(audit_env, monkeypatch):
    env = audit_env
    service = _build_service(env.db, FakeTMDB())
    _patch_tail(monkeypatch, service)
    unavailable = AsyncMock(side_effect=ValueError("cloud unavailable"))
    monkeypatch.setattr(service, "_get_storage_provider", lambda _: SimpleNamespace(ensure_directory=unavailable))
    source = StorageLocator(provider="115", path="/115网盘/in/ep.mkv", file_id="11", parent_id="10", is_dir=False)
    output = StorageLocator(provider="115", path="/115网盘/out", file_id="20", is_dir=True)
    nfo = env.metadata / "Test Show (2024)" / "Season 1" / "Test Show - S01E01 - Episode 1.nfo"
    nfo.parent.mkdir(parents=True)
    nfo.write_text("old metadata")
    result = await service.scrape_by_id(ScrapeByIdRequest(
        file_path=source.path, tmdb_id=123, season=1, episode=1, file_locator=source,
        output_locator=output, metadata_dir=str(env.metadata), link_mode="move",
    ))
    unavailable.assert_awaited_once()
    assert result.status.value != "success"
    assert nfo.read_text() == "old metadata"


@pytest.mark.asyncio
async def test_shutdown_flush_overrides_log_backoff():
    service = SimpleNamespace(batch_insert=AsyncMock(side_effect=[OSError("busy"), None]))
    handler = DatabaseLogHandler(service, flush_interval=60)
    handler.start()
    try:
        handler._batch.append({"message": "queued"})
        await handler._flush()
        await handler.drain()
        assert service.batch_insert.await_count == 2
        assert not handler._batch
    finally:
        handler.close()


@pytest.mark.asyncio
async def test_scheduler_rename_preserves_due_occurrence(audit_env):
    scheduler = SchedulerService(audit_env.db)
    task = await scheduler.create_task(ScheduledTaskCreate(name="old", folder_path="/in", cron_expression="0 0 * * *"))
    due = datetime.now() - timedelta(minutes=1)
    await scheduler._repo.update_task(task_id=task.id, name=task.name, folder_path=task.folder_path,
                                      cron_expression=task.cron_expression, enabled=True, next_run=due.isoformat())
    updated = await scheduler.update_task(task.id, ScheduledTaskUpdate(name="new"))
    assert updated.next_run == due
    assert (await scheduler._repo.claim_due(datetime.now()))["id"] == task.id


@pytest.mark.asyncio
async def test_watcher_failed_baseline_is_reinitialized_before_delivery(monkeypatch):
    from server.application import watcher_service

    seen = []
    folder = WatchedFolder(id="f", path="/115网盘/in", file_id="10", provider="115", mode="event")
    watcher = P115EventStrategy(folder, lambda path, _: seen.append(path))
    event = {"update_time": 12, "behavior_type": "upload_file", "items": [
        {"parent_id": "10", "file_id": "20", "file_name": "old.mkv", "ico": "mkv"}]}
    page = {"state": True, "data": {"list": [event]}}
    client = SimpleNamespace(life_list=AsyncMock(side_effect=[{"state": False}, page, page]))
    monkeypatch.setattr(watcher, "_get_client", AsyncMock(return_value=client))
    monkeypatch.setattr(watcher, "_collect_dir_ids", AsyncMock())
    async def wait(_):
        if client.life_list.await_count >= 3:
            watcher._running = False
    monkeypatch.setattr(watcher_service.asyncio, "sleep", wait)
    await watcher.start()
    await watcher._scan_task
    assert not seen
    assert watcher._last_update_time == 12


@pytest.mark.asyncio
async def test_watcher_retries_directory_refresh_before_advancing_event(monkeypatch):
    from server.application import watcher_service
    from server.domain.integration.p115_service import P115Service

    seen = []
    folder = WatchedFolder(id="f", path="/115网盘/in", file_id="10", provider="115", mode="event")
    watcher = P115EventStrategy(folder, lambda path, _: seen.append(path))
    watcher._last_update_time = 10
    watcher._running = True
    watcher._loop_count = 1
    event = {"update_time": 12, "behavior_type": "upload_file", "items": [
        {"parent_id": "11", "file_id": "20", "file_name": "new.mkv", "ico": "mkv"}]}
    client = SimpleNamespace(life_list=AsyncMock(return_value={"state": True, "data": {"list": [event]}}))
    monkeypatch.setattr(watcher, "_get_client", AsyncMock(return_value=client))
    monkeypatch.setattr(P115Service, "browse", AsyncMock(side_effect=[
        OSError("offline"),
        {"entries": [{"is_dir": True, "file_id": "11", "name": "child", "path": folder.path + "/child"}]},
        {"entries": []},
    ]))
    async def wait(_):
        if client.life_list.await_count >= 2:
            watcher._running = False
        else:
            assert watcher._last_update_time == 10
    monkeypatch.setattr(watcher_service.asyncio, "sleep", wait)
    await watcher._event_loop()
    assert seen == [folder.path + "/child/new.mkv"]
    assert watcher._last_update_time == 12


@pytest.mark.asyncio
async def test_invalid_disabled_schedule_returns_client_error(audit_env):
    from fastapi import HTTPException
    from server.api.v1.scheduler import toggle_task

    service = SchedulerService(audit_env.db)
    task = await service.create_task(ScheduledTaskCreate(
        name="never", folder_path="/in", cron_expression="0 0 31 2 *", enabled=False))
    with pytest.raises(HTTPException) as caught:
        await toggle_task(task.id, service)
    assert caught.value.status_code == 400
    assert not (await service.get_task(task.id)).enabled
