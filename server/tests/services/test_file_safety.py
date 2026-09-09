"""File safety regressions; run only in GitHub Actions."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from server.api import history as history_api
from server.core.path_security import PathSecurityError
from server.models.manual_job import ManualJobAdvancedSettings
from server.models.organize import OrganizeMode
from server.models.rename import RenameRequest
from server.models.scraper import ScrapeByIdRequest, ScrapeRequest, ScrapeStatus
from server.models.history import TaskStatus
from server.models.storage import StorageLocator, StorageProvider
from server.services import file_operations
from server.services.rename_service import RenameService
from server.services.scraper_service import ScraperService
from server.services.scraper_media import ScraperMediaMixin
from server.services.subtitle_service import SubtitleService
from server.services.template_service import TemplateService


@pytest.mark.parametrize("mode", list(OrganizeMode))
def test_overwrite_publishes_complete_file(tmp_path, mode):
    source, destination = tmp_path / "source", tmp_path / "destination"
    source.write_bytes(b"new content")
    destination.write_bytes(b"old content")
    file_operations.publish_file(source, destination, mode, overwrite=True)
    assert destination.read_bytes() == b"new content"
    assert source.exists() == (mode != OrganizeMode.MOVE)
    assert destination.is_symlink() == (mode == OrganizeMode.SYMLINK)
    if mode == OrganizeMode.HARDLINK:
        assert source.stat().st_ino == destination.stat().st_ino
    assert not list(tmp_path.glob(".mhti-transfer-*"))


@pytest.mark.parametrize("mode", [OrganizeMode.COPY, OrganizeMode.MOVE])
@pytest.mark.parametrize("failure", ["copy", "commit"])
def test_rename_overwrite_failure_preserves_both_files(tmp_path, monkeypatch, mode, failure):
    service = RenameService(TemplateService(db_path=tmp_path / "config.db"))
    source = tmp_path / "episode.mkv"
    source.write_bytes(b"new content")
    request = RenameRequest(source_path=str(source), title="Show", season=1, episode=1,
                            output_dir=str(tmp_path / "output"), link_mode=mode,
                            conflict_action="overwrite")
    target = Path(service.preview_rename(request).dest_path)
    target.parent.mkdir(parents=True)
    target.write_bytes(b"old content")

    def fail_copy(src, dst):
        Path(dst).write_bytes(b"partial")
        raise OSError("disk full")

    def fail_commit(src, dst):
        raise PermissionError("publication denied")

    if failure == "copy":
        monkeypatch.setattr(file_operations.shutil, "copy2", fail_copy)
    else:
        monkeypatch.setattr(file_operations.os, "replace", fail_commit)
    result = service.execute_rename(request)
    assert not result.success
    assert source.read_bytes() == b"new content"
    assert target.read_bytes() == b"old content"
    assert not list(target.parent.glob(".mhti-transfer-*"))


@pytest.mark.parametrize("mode", list(OrganizeMode))
def test_no_overwrite_never_replaces_existing_file(tmp_path, mode):
    source, destination = tmp_path / "source", tmp_path / "destination"
    source.write_bytes(b"new")
    destination.write_bytes(b"old")
    with pytest.raises(FileExistsError):
        file_operations.publish_file(source, destination, mode)
    assert source.read_bytes() == b"new"
    assert destination.read_bytes() == b"old"
    assert not list(tmp_path.glob(".mhti-transfer-*"))


@pytest.mark.parametrize("mode", list(OrganizeMode))
def test_subtitles_follow_video_mode_without_renaming_source(tmp_path, mode):
    incoming, output = tmp_path / "incoming", tmp_path / "output"
    incoming.mkdir()
    output.mkdir()
    subtitle = incoming / "episode.chs.srt"
    subtitle.write_text("subtitle", encoding="utf-8")
    service = ScraperMediaMixin()
    service.subtitle_service = SubtitleService()
    result = service._process_subtitles(str(incoming / "episode.mkv"),
                                        str(output / "Show S01E01.mkv"), mode)
    target = output / "Show S01E01.chs.srt"
    assert result == [str(target)]
    assert target.read_text() == "subtitle"
    assert subtitle.exists() == (mode != OrganizeMode.MOVE)
    assert not (incoming / target.name).exists()
    if mode == OrganizeMode.HARDLINK:
        assert subtitle.stat().st_ino == target.stat().st_ino
    if mode == OrganizeMode.SYMLINK:
        assert target.is_symlink()


def test_subtitle_conflict_preserves_source_and_target(tmp_path):
    incoming, output = tmp_path / "incoming", tmp_path / "output"
    incoming.mkdir()
    output.mkdir()
    source, target = incoming / "episode.srt", output / "Show.srt"
    source.write_text("new")
    target.write_text("old")
    service = ScraperMediaMixin()
    service.subtitle_service = SubtitleService()
    assert service._process_subtitles(str(incoming / "episode.mkv"),
                                      str(output / "Show.mkv"), OrganizeMode.MOVE) == []
    assert source.read_text() == "new"
    assert target.read_text() == "old"


@pytest.mark.asyncio
@pytest.mark.parametrize("manual", [False, True])
async def test_invalid_metadata_directory_rejected_before_scraping(tmp_path, monkeypatch, manual):
    allowed = tmp_path / "media"
    allowed.mkdir()
    source = allowed / "episode.strm"
    source.write_text("https://example.invalid/video")
    monkeypatch.setenv("MHTI_ALLOWED_MEDIA_ROOTS", str(allowed))
    service = object.__new__(ScraperService)
    service.tmdb_service = Mock()
    service.tmdb_service.get_series_by_api = AsyncMock()
    data = dict(file_path=str(source), metadata_dir=str(tmp_path / "outside"))
    if manual:
        result = await service.scrape_by_id(ScrapeByIdRequest(**data, tmdb_id=123, season=1, episode=1))
    else:
        result = await service.scrape_file(ScrapeRequest(**data))
    assert result.status == ScrapeStatus.MOVE_FAILED
    assert source.exists()
    assert not (tmp_path / "outside").exists()
    service.tmdb_service.get_series_by_api.assert_not_awaited()


def test_nfo_rejects_symlink_escape(tmp_path, monkeypatch):
    allowed = tmp_path / "media"
    allowed.mkdir()
    outside = tmp_path / "outside.nfo"
    outside.write_text("old")
    target = allowed / "episode.nfo"
    target.symlink_to(outside)
    monkeypatch.setenv("MHTI_ALLOWED_MEDIA_ROOTS", str(allowed))
    with pytest.raises(PathSecurityError):
        file_operations.write_metadata_text(target, "new")
    assert outside.read_text() == "old"


def test_nfo_commit_failure_preserves_existing_metadata(tmp_path, monkeypatch):
    target = tmp_path / "episode.nfo"
    target.write_text("old")
    monkeypatch.setattr(file_operations.os, "replace", Mock(side_effect=OSError("failed")))
    with pytest.raises(OSError):
        file_operations.write_metadata_text(target, "new")
    assert target.read_text() == "old"
    assert not list(tmp_path.glob(".mhti-nfo-*"))


def test_nfo_write_is_readable_and_complete(tmp_path):
    target = tmp_path / "episode.nfo"
    file_operations.write_metadata_text(target, "<episode />")
    assert target.read_text() == "<episode />"
    assert target.stat().st_mode & 0o777 == 0o644


@pytest.mark.asyncio
async def test_retry_restores_original_job_options(tmp_path, monkeypatch):
    from server.services.scrape_job_service import ScrapeJobService

    record = SimpleNamespace(scrape_job_id="original", status=TaskStatus.TIMEOUT,
                             folder_path=str(tmp_path / "episode.mkv"), conflict_data=None)
    settings = ManualJobAdvancedSettings(use_global_metadata=False, nfo_enabled=False)
    job = SimpleNamespace(file_locator=None, output_locator=None, metadata_locator=None,
                          allow_local_output=False, output_dir="/output", metadata_dir="/output/nfo",
                          link_mode=OrganizeMode.COPY, advanced_settings=settings)
    monkeypatch.setattr(ScrapeJobService, "get_job", AsyncMock(return_value=job))
    execute = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(history_api, "_execute_scrape_and_update", execute)
    history = Mock(get_record=AsyncMock(return_value=record))
    await history_api.retry_scrape("record", history_api.RetryRequest(tmdb_id=1, season=1, episode=2), history)
    request = execute.await_args.args[2]
    assert request.link_mode == OrganizeMode.COPY
    assert request.output_dir == "/output"
    assert request.metadata_dir == "/output/nfo"
    assert request.advanced_settings == settings


@pytest.mark.asyncio
async def test_missing_original_job_blocks_retry(monkeypatch):
    from fastapi import HTTPException
    from server.services.scrape_job_service import ScrapeJobService

    monkeypatch.setattr(ScrapeJobService, "get_job", AsyncMock(return_value=None))
    with pytest.raises(HTTPException) as error:
        await history_api._restore_locators_from_scrape_job(SimpleNamespace(scrape_job_id="missing"))
    assert error.value.status_code == 409


def test_cloud_metadata_locator_is_rejected():
    locator = StorageLocator(provider=StorageProvider.P115, path="/115网盘/nfo", is_dir=True)
    with pytest.raises(PathSecurityError):
        ScraperService._validate_metadata_directory(None, locator)
