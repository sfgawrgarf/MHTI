"""Background-job regressions: preserve S00 and classify upstream outages."""

import asyncio
from datetime import datetime
from types import SimpleNamespace
from threading import Event
from unittest.mock import AsyncMock, Mock

import pytest

from server.models.history import ConflictType, TaskStatus
from server.models.scrape_job import ScrapeJob, ScrapeJobSource, ScrapeJobStatus
from server.models.scraper import ScrapeResult, ScrapeStatus
from server.services.scrape_job_service import _execute_scrape_job


@pytest.fixture
def worker(monkeypatch, temp_dir):
    job = ScrapeJob(
        id="test-job", file_path=str(temp_dir / "Known Title.strm"),
        output_dir=str(temp_dir), source=ScrapeJobSource.MANUAL, created_at=datetime.now(),
    )
    service = Mock(claim_job=AsyncMock(return_value=True), get_job=AsyncMock(return_value=job),
                   update_job=AsyncMock())
    scraper = Mock(scrape_file=AsyncMock(), scrape_by_id=AsyncMock())
    history = Mock(
        create_record=AsyncMock(return_value=SimpleNamespace(id="test-history")),
        update_record=AsyncMock(), update_scrape_logs=AsyncMock(),
        update_record_on_success=AsyncMock(), flush_and_clear_log_cache=AsyncMock(),
    )
    notifier = Mock(notify_progress=AsyncMock(), notify_failed=AsyncMock(), notify_need_action=AsyncMock(),
                    notify_completed=AsyncMock(), notify_cancelled=AsyncMock())
    config = Mock(get_system_config=AsyncMock(return_value=SimpleNamespace(task_timeout=30)))
    monkeypatch.setattr("server.core.container.get_scraper_service", lambda: scraper)
    monkeypatch.setattr("server.services.history_service.HistoryService", lambda: history)
    monkeypatch.setattr("server.services.config_service.ConfigService", lambda: config)
    monkeypatch.setattr("server.services.scrape_job_service.get_notifier", lambda: notifier)
    monkeypatch.setattr("server.services.scrape_job_service.calculate_fingerprint", lambda path: None)
    return job, service, scraper, history, notifier


@pytest.mark.asyncio
@pytest.mark.parametrize("season, expected", [(0, 0), (None, 1), (5, 5)])
async def test_correction_request_preserves_explicit_season(worker, season, expected):
    job, service, scraper, _, _ = worker
    job.correction_tmdb_id = 123
    job.correction_season = season
    job.correction_episode = 1
    scraper.scrape_by_id.return_value = ScrapeResult(
        file_path=job.file_path, status=ScrapeStatus.API_FAILED, message="服务异常",
    )
    await _execute_scrape_job(service, job.id)
    scraper.scrape_by_id.assert_awaited_once()
    request = scraper.scrape_by_id.call_args.args[0]
    assert request.season == expected
    assert request.episode == 1
    scraper.scrape_file.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [ScrapeStatus.SEARCH_FAILED, ScrapeStatus.API_FAILED])
async def test_upstream_failure_is_failed_not_manual_id_conflict(worker, status):
    job, service, scraper, history, notifier = worker
    scraper.scrape_file.return_value = ScrapeResult(
        file_path=job.file_path, status=status, message="TMDB 请求过于频繁，请稍后重试",
    )
    await _execute_scrape_job(service, job.id)
    assert history.update_record.call_args.kwargs["status"] == TaskStatus.FAILED
    assert "conflict_type" not in history.update_record.call_args.kwargs
    assert service.update_job.call_args.kwargs["status"] == ScrapeJobStatus.FAILED
    notifier.notify_need_action.assert_not_awaited()
    notifier.notify_failed.assert_awaited_once_with(job.id, scraper.scrape_file.return_value.message)


@pytest.mark.asyncio
async def test_genuine_no_match_still_allows_manual_id_selection(worker):
    job, service, scraper, history, notifier = worker
    scraper.scrape_file.return_value = ScrapeResult(
        file_path=job.file_path, status=ScrapeStatus.NO_MATCH, message="未找到匹配剧集",
    )
    await _execute_scrape_job(service, job.id)
    assert history.update_record.call_args.kwargs["status"] == TaskStatus.PENDING_ACTION
    assert history.update_record.call_args.kwargs["conflict_type"] == ConflictType.NO_MATCH
    assert service.update_job.call_args.kwargs["status"] == ScrapeJobStatus.PENDING_ACTION
    notifier.notify_failed.assert_not_awaited()
    assert notifier.notify_need_action.call_args.args[1] == "need_tmdb_id"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [ScrapeStatus.SUCCESS, ScrapeStatus.FILE_CONFLICT])
async def test_manual_continuation_updates_original_history_and_forwards_choice(worker, status):
    job, service, scraper, history, notifier = worker
    job.continuation_history_id = "old-history"
    job.correction_tmdb_id = 123
    job.correction_season = 0
    job.correction_episode = 1
    job.file_action = "overwrite"
    job.skip_emby_check = True
    job.selection_log = "用户选择 S00E01"
    history.get_record = AsyncMock(return_value=SimpleNamespace(id="old-history", scrape_logs=[]))
    scraper.scrape_by_id.return_value = ScrapeResult(
        file_path=job.file_path, status=status, selected_id=123, parsed_season=0,
        parsed_episode=1, dest_path="/library/show.strm",
    )
    await _execute_scrape_job(service, job.id)
    history.create_record.assert_not_awaited()
    request = scraper.scrape_by_id.call_args.args[0]
    assert request.file_action == "overwrite"
    assert request.skip_emby_check is True
    assert request.season == 0
    assert history.update_scrape_logs.call_args.args[0] == "old-history"
    if status == ScrapeStatus.SUCCESS:
        update = history.update_record_on_success.call_args
        assert update.args[0] == "old-history"
        assert update.kwargs["folder_path"].endswith("=> /library/show.strm")
        assert update.kwargs["season_number"] == 0
        notifier.notify_completed.assert_awaited_once()
    else:
        update = history.update_record.call_args
        assert update.args[0] == "old-history"
        assert update.kwargs["status"] == TaskStatus.PENDING_ACTION
        assert update.kwargs["conflict_data"]["season"] == 0
        assert update.kwargs["conflict_data"]["dest_path"] == "/library/show.strm"


@pytest.mark.asyncio
async def test_cancelled_worker_records_cancellation(worker):
    job, service, scraper, history, notifier = worker
    scraper.scrape_file.side_effect = asyncio.CancelledError
    with pytest.raises(asyncio.CancelledError):
        await _execute_scrape_job(service, job.id)
    assert service.update_job.call_args.kwargs["status"] == ScrapeJobStatus.CANCELLED
    assert history.update_record.call_args.kwargs["status"] == TaskStatus.CANCELLED
    history.flush_and_clear_log_cache.assert_awaited_once()
    notifier.notify_cancelled.assert_awaited_once()


@pytest.mark.asyncio
async def test_timeout_then_shutdown_still_drains_file_thread(worker):
    from server.services.config_service import ConfigService
    from server.services.file_io import check_file_cancelled, run_file_io

    job, service, scraper, history, _ = worker
    config = await ConfigService().get_system_config()
    config.task_timeout = 0.03
    started, release, stopped = Event(), Event(), Event()

    def transfer():
        started.set()
        try:
            assert release.wait(3)
            check_file_cancelled()
        finally:
            stopped.set()

    async def scrape(*args, **kwargs):
        await run_file_io(transfer)

    scraper.scrape_file.side_effect = scrape
    task = asyncio.create_task(_execute_scrape_job(service, job.id))
    try:
        async with asyncio.timeout(2):
            while not started.is_set():
                await asyncio.sleep(0.001)
        await asyncio.sleep(0.06)
        task.cancel()  # shutdown arrives after the execution timeout
        await asyncio.sleep(0)
        assert not task.done()
        history.update_record.assert_not_awaited()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert stopped.is_set()
        assert service.update_job.call_args.kwargs["status"] == ScrapeJobStatus.CANCELLED
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
