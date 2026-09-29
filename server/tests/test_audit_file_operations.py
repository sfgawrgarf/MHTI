"""Regression cases for failed publication and subtitle source preservation."""

from pathlib import Path

import pytest

from server.infrastructure.file_operations import publish_file
from server.models.organize import OrganizeMode
from server.domain.artifacts.subtitle_service import SubtitleService
from server.application.scraping.media_pipeline import ScraperMediaPipeline


@pytest.mark.parametrize("failure", ["copy2", "replace"])
def test_failed_overwrite_preserves_both_files(tmp_path, monkeypatch, failure):
    from server.infrastructure import file_operations

    source, target = tmp_path / "source", tmp_path / "target"
    source.write_bytes(b"new")
    target.write_bytes(b"old")

    def fail(*args, **kwargs):
        raise OSError("injected failure")

    module = file_operations.shutil if failure == "copy2" else file_operations.os
    monkeypatch.setattr(module, failure, fail)
    with pytest.raises(OSError):
        publish_file(source, target, OrganizeMode.COPY, overwrite=True)
    assert source.read_bytes() == b"new"
    assert target.read_bytes() == b"old"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["source", "target"]


@pytest.mark.parametrize("mode", list(OrganizeMode))
def test_conflict_never_removes_source_or_target(tmp_path, mode):
    source, target = tmp_path / "source", tmp_path / "target"
    source.write_bytes(b"new")
    target.write_bytes(b"old")
    with pytest.raises(FileExistsError):
        publish_file(source, target, mode)
    assert source.read_bytes() == b"new"
    assert target.read_bytes() == b"old"


def test_nested_association_keeps_paths_and_directory_scope(tmp_path):
    for name in ("one", "two"):
        folder = tmp_path / name
        folder.mkdir()
        (folder / "EP01.mkv").write_bytes(b"video")
        (folder / "EP01.en.srt").write_text(name)
    result = SubtitleService().associate_subtitles(str(tmp_path))
    assert {a.video for a in result.associations} == {"one/EP01.mkv", "two/EP01.mkv"}
    for association in result.associations:
        assert Path(association.video_path).is_file()
        assert len(association.subtitles) == 1
        assert Path(association.subtitles[0].path).parent == Path(association.video_path).parent


@pytest.mark.parametrize("mode", [OrganizeMode.COPY, OrganizeMode.HARDLINK, OrganizeMode.SYMLINK])
def test_subtitles_preserve_source_for_non_move_modes(tmp_path, mode):
    source_dir, dest_dir = tmp_path / "in", tmp_path / "out"
    source_dir.mkdir()
    dest_dir.mkdir()
    source = source_dir / "EP01.srt"
    source.write_text("subtitle")
    pipeline = ScraperMediaPipeline(image_service=None, subtitle_service=SubtitleService(), emby_service=None)
    paths = pipeline.process_subtitles(str(source_dir / "EP01.mkv"), str(dest_dir / "Episode.mkv"), mode)
    assert source.read_text() == "subtitle"
    assert len(paths) == 1
    assert Path(paths[0]).read_text() == "subtitle"


def test_subtitle_conflict_preserves_source_and_existing_target(tmp_path):
    source_dir, dest_dir = tmp_path / "in", tmp_path / "out"
    source_dir.mkdir()
    dest_dir.mkdir()
    source = source_dir / "EP01.srt"
    source.write_text("new")
    target = dest_dir / "Episode.srt"
    target.write_text("old")
    pipeline = ScraperMediaPipeline(image_service=None, subtitle_service=SubtitleService(), emby_service=None)
    assert pipeline.process_subtitles(str(source_dir / "EP01.mkv"), str(dest_dir / "Episode.mkv")) == []
    assert source.read_text() == "new"
    assert target.read_text() == "old"


@pytest.mark.asyncio
async def test_scan_runs_outside_event_loop(tmp_path, monkeypatch):
    import threading
    from unittest.mock import AsyncMock
    from server.application.library import FolderScanUseCase
    from server.domain.media.file_service import FileService

    service = FileService()
    loop_thread = threading.get_ident()

    def scan(*args):
        assert threading.get_ident() != loop_thread
        return []

    monkeypatch.setattr(service, "scan_folder", scan)
    await FolderScanUseCase(service, AsyncMock()).scan(str(tmp_path), None, False)
