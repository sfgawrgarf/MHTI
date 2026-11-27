"""Regression checks for the November main-branch review."""

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from server.application.ai_provider_service import AIProviderError, AIProviderService
from server.application.scheduler_service import SchedulerService
from server.application.watcher_service import P115EventStrategy
from server.infrastructure.file_journal import RecoveryConflict, signature
from server.infrastructure.file_operations import remove_moved_source
from server.infrastructure.log_handler import DatabaseLogHandler
from server.infrastructure.log_security import SafeLogFilter
from server.models.history import TaskStatus
from server.models.scheduler import ScheduledTaskCreate
from server.models.scrape_job import ScrapeJobCreate, ScrapeJobStatus
from server.models.scraped_file import ScrapedFileCreate
from server.models.watcher import WatchedFolder
from server.tests.application.test_audit_remediation import audit_env, record_for  # noqa: F401


def test_request_log_redacts_query_in_url_arguments():
    record = logging.LogRecord("httpx", logging.INFO, "", 0, "GET %s",
                               (httpx.URL("https://example.invalid/3?api_key=test-value&page=2"),), None)
    SafeLogFilter().filter(record)
    assert "test-value" not in record.getMessage()
    assert "page=2" in record.getMessage()


def test_move_cleanup_retains_a_changed_source(tmp_path):
    source = tmp_path / "episode.mkv"
    source.write_bytes(b"old")
    expected = signature(source)
    source.write_bytes(b"new download")
    with pytest.raises(RecoveryConflict):
        remove_moved_source(source, expected)
    assert source.read_bytes() == b"new download"


@pytest.mark.asyncio
async def test_history_delete_retains_new_source(audit_env):
    env = audit_env
    record = await record_for(env, str(env.source))
    await env.scraped.add_record(ScrapedFileCreate(source_path=str(env.source),
                                 file_size=env.source.stat().st_size, history_record_id=record.id))
    env.source.write_bytes(b"newly downloaded file")
    response = await env.files.delete_files(record.id, "source")
    assert response.deleted == 0
    assert env.source.read_bytes() == b"newly downloaded file"


@pytest.mark.asyncio
async def test_undo_restores_terminal_job_and_history(audit_env):
    env = audit_env
    job = await env.jobs.create_job(ScrapeJobCreate(file_path=str(env.source), output_dir=str(env.output)))
    await env.jobs.update_job(job.id, status=ScrapeJobStatus.FAILED, error_message="lookup failed")
    record = await record_for(env, str(env.source), status=TaskStatus.FAILED, job_id=job.id)
    assert await env.history.delete_record(record.id)
    assert (await env.jobs.get_job(job.id)).status == ScrapeJobStatus.DELETED
    assert (await env.history.undo_last()).undone
    restored = await env.jobs.get_job(job.id)
    assert restored.status == ScrapeJobStatus.FAILED
    assert restored.error_message == "lookup failed"


@pytest.mark.asyncio
async def test_impossible_schedule_is_rejected(audit_env):
    scheduler = SchedulerService(audit_env.db)
    with pytest.raises(ValueError, match="Cron"):
        await scheduler.create_task(ScheduledTaskCreate(
            name="no occurrence", folder_path=str(audit_env.source.parent), cron_expression="0 0 31 2 *"))


@pytest.mark.asyncio
async def test_event_pages_reach_watermark():
    strategy = P115EventStrategy(WatchedFolder(id="a", path="/115网盘/in", provider="115", file_id="root", mode="event"), lambda *_: None)
    strategy._last_update_time = 10
    client = SimpleNamespace(life_list=AsyncMock(side_effect=[
        {"state": True, "data": {"list": [{"update_time": 12}], "last_data": "page2"}},
        {"state": True, "data": {"list": [{"update_time": 9}], "last_data": "page3"}},
    ]))
    events = await strategy._read_event_pages(client)
    assert [event["update_time"] for event in events] == [12, 9]
    assert client.life_list.await_count == 2


@pytest.mark.parametrize("content", [None, [], " "])
def test_ai_non_text_response_has_provider_error(content):
    with pytest.raises(AIProviderError):
        AIProviderService._json_from_response(content)


@pytest.mark.asyncio
async def test_transient_database_log_failure_preserves_batch():
    service = SimpleNamespace(batch_insert=AsyncMock(side_effect=[OSError("busy"), None]))
    handler = DatabaseLogHandler(service, flush_interval=0)
    handler._batch.append({"message": "first"})
    await handler._flush()
    assert len(handler._batch) == 1
    await handler._flush()
    assert not handler._batch
    service.batch_insert.assert_awaited_with([{"message": "first"}])
    handler.close()


@pytest.mark.asyncio
async def test_log_service_retries_on_shutdown_and_keeps_searchable_fields(audit_env, monkeypatch):
    from datetime import datetime, timezone

    from server.domain.system.log_service import LogService
    from server.infrastructure.repositories.log_repository import LogRepository
    from server.models.log import LogEntryCreate, LogQuery

    service = LogService()
    repo = LogRepository(audit_env.db)
    service._repo = repo
    service._batch_size = 1
    entry = LogEntryCreate(timestamp=datetime.now(timezone.utc), level="INFO", logger="audit",
                           message="retried write", extra_data={"item": 3}, request_id="test")
    await service.start()
    with monkeypatch.context() as patch:
        patch.setattr(repo, "insert_batch", AsyncMock(side_effect=OSError("database busy")))
        await service.add_log(entry)
    await service.stop()

    items, total = await service.get_logs(LogQuery(level="INFO", search="retried", logger="audit"))
    assert total == 1
    assert items[0].extra_data == {"item": 3}
    assert items[0].request_id == "test"
    stats = await service.get_stats()
    assert stats.by_logger == {"audit": 1}
    assert stats.oldest_entry == entry.timestamp


@pytest.mark.asyncio
@pytest.mark.parametrize("has_episode", [True, False])
async def test_emby_conflict_checks_second_page_in_selected_library(audit_env, monkeypatch, has_episode):
    from server.domain.integration.emby_service import EmbyService
    from server.models.emby import ConflictCheckRequest, ConflictType, EmbyConfig

    config = EmbyConfig(enabled=True, server_url="http://emby.test", api_key="test",
                        library_ids=["library"])
    service = EmbyService(config_service=audit_env.config)
    monkeypatch.setattr(service, "get_config", AsyncMock(return_value=config))
    offsets = []

    def respond(request):
        params = request.url.params
        if params["IncludeItemTypes"] == "Series":
            assert params["ParentId"] == "library"
            offset = int(params.get("StartIndex", 0))
            offsets.append(offset)
            if offset == 0:
                items = [{"Id": str(i), "Name": "Other"} for i in range(20)]
            else:
                items = [{"Id": "show", "Name": "Show", "ProviderIds": {"Tmdb": "123"}}]
            return httpx.Response(200, json={"Items": items, "TotalRecordCount": 21})
        assert params["ParentId"] == "show"
        items = [{"Id": "ep", "Name": "Pilot", "ParentIndexNumber": 1, "IndexNumber": 1}]
        return httpx.Response(200, json={"Items": items if has_episode else []})

    monkeypatch.setattr(service, "_get_client", lambda _: httpx.AsyncClient(
        base_url=config.server_url, transport=httpx.MockTransport(respond)))
    result = await service.check_conflict(ConflictCheckRequest(series_name="Show", tmdb_id=123, season=1, episode=1))
    assert offsets == [0, 20]
    assert result.conflict_type == (ConflictType.EPISODE_EXISTS if has_episode else ConflictType.SERIES_EXISTS)
    assert result.existing_series.id == "show"
