"""Unit tests for RenameService."""

import pytest
import tempfile
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from server.models.template import NamingTemplate
from server.services.config_service import ConfigService
from server.services.rename_service import RenameService
from server.services.template_service import TemplateService
from server.models.organize import OrganizeMode
from server.models.rename import RenameRequest, BatchRenameRequest
from server.models.history import ScrapeLogStep
from server.models.scraper import ScrapeByIdRequest, ScrapeResult, ScrapeStatus
from server.models.storage import StorageLocator, StorageProvider
from server.models.tmdb import TMDBEpisode, TMDBSeason, TMDBSeries


@pytest.fixture
def rename_service(temp_db):
    """Provide a RenameService instance."""
    return RenameService(template_service=TemplateService(db_path=temp_db))


@pytest.fixture
def temp_dir():
    """Create a temporary directory for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def sample_video(temp_dir):
    """Create a sample video file for testing."""
    video_path = Path(temp_dir) / "test_video.mp4"
    video_path.write_bytes(b"fake video content")
    return str(video_path)


class TestRenameServicePreview:
    """Tests for preview_rename method."""

    def test_preview_basic(self, rename_service, sample_video):
        """Test basic preview functionality."""
        request = RenameRequest(
            source_path=sample_video,
            title="权力的游戏",
            season=1,
            episode=1,
            episode_title="凛冬将至",
        )

        preview = rename_service.preview_rename(request)

        assert preview.source_path == sample_video
        assert "权力的游戏" in preview.dest_path
        assert "S01E01" in preview.dest_path
        assert "凛冬将至" in preview.new_filename
        assert ".mp4" in preview.new_filename

    def test_preview_with_output_dir(self, rename_service, sample_video, temp_dir):
        """Test preview with custom output directory."""
        output_dir = Path(temp_dir) / "output"
        request = RenameRequest(
            source_path=sample_video,
            title="Breaking Bad",
            season=5,
            episode=16,
            output_dir=str(output_dir),
        )

        preview = rename_service.preview_rename(request)

        assert str(output_dir) in preview.dest_path
        assert "Breaking Bad" in preview.dest_path
        assert "Season 5" in preview.dest_folder  # Default template uses {season} not {season:02d}
        assert "S05E16" in preview.new_filename

    def test_preview_creates_dirs_list(self, rename_service, sample_video, temp_dir):
        """Test that preview lists directories to be created."""
        output_dir = Path(temp_dir) / "new_output"
        request = RenameRequest(
            source_path=sample_video,
            title="Test Show",
            season=1,
            episode=1,
            output_dir=str(output_dir),
        )

        preview = rename_service.preview_rename(request)

        assert len(preview.will_create_dirs) > 0
        assert any("Test Show" in d for d in preview.will_create_dirs)

    def test_preview_without_episode_title(self, rename_service, sample_video):
        """Test preview without episode title."""
        request = RenameRequest(
            source_path=sample_video,
            title="Test Show",
            season=2,
            episode=5,
        )

        preview = rename_service.preview_rename(request)

        assert "S02E05" in preview.new_filename
        assert ".mp4" in preview.new_filename

    @pytest.mark.asyncio
    async def test_preview_uses_saved_naming_template(self, temp_dir, sample_video):
        """Test preview uses persisted naming template configuration."""
        db_path = Path(temp_dir) / "config.db"
        config_service = ConfigService(db_path=db_path)
        await config_service.save_naming_config(
            NamingTemplate(
                series_folder="{title}",
                season_folder="S{season:02d}",
                episode_file="{title}.S{season:02d}E{episode:02d}",
            )
        )
        rename_service = RenameService(template_service=TemplateService(db_path=db_path))

        request = RenameRequest(
            source_path=sample_video,
            title="Test Show",
            season=2,
            episode=5,
            output_dir=temp_dir,
        )

        preview = rename_service.preview_rename(request)

        assert preview.dest_folder.endswith(str(Path("Test Show") / "S02"))
        assert preview.new_filename == "Test Show.S02E05.mp4"


class TestRenameServiceExecute:
    """Tests for execute_rename method."""

    def test_execute_rename_success(self, rename_service, sample_video, temp_dir):
        """Test successful rename execution."""
        output_dir = Path(temp_dir) / "output"
        request = RenameRequest(
            source_path=sample_video,
            title="Test Show",
            season=1,
            episode=1,
            output_dir=str(output_dir),
        )

        result = rename_service.execute_rename(request)

        assert result.success is True
        assert Path(result.dest_path).exists()
        assert not Path(sample_video).exists()  # Original moved

    def test_execute_rename_with_backup(self, rename_service, sample_video, temp_dir):
        """Test rename with backup creation."""
        output_dir = Path(temp_dir) / "output"
        request = RenameRequest(
            source_path=sample_video,
            title="Test Show",
            season=1,
            episode=1,
            output_dir=str(output_dir),
        )

        result = rename_service.execute_rename(request, create_backup=True)

        assert result.success is True
        assert result.backup_path is not None
        assert Path(result.backup_path).exists()

    def test_execute_rename_source_not_found(self, rename_service, temp_dir):
        """Test rename with non-existent source."""
        request = RenameRequest(
            source_path="/nonexistent/video.mp4",
            title="Test Show",
            season=1,
            episode=1,
        )

        result = rename_service.execute_rename(request)

        assert result.success is False
        assert "not found" in result.error.lower()

    def test_execute_rename_destination_exists(self, rename_service, temp_dir):
        """Test rename when destination already exists."""
        # Create source file
        source_path = Path(temp_dir) / "source.mp4"
        source_path.write_bytes(b"source content")

        # First, do a preview to get exact dest filename
        request = RenameRequest(
            source_path=str(source_path),
            title="Test Show",
            season=1,
            episode=1,
            output_dir=str(Path(temp_dir) / "output"),
        )
        preview = rename_service.preview_rename(request)

        # Create destination structure with the exact expected file
        dest_path = Path(preview.dest_path)
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        dest_path.write_bytes(b"existing content")

        result = rename_service.execute_rename(request)

        assert result.success is False
        assert "exists" in result.error.lower()

    def test_execute_rename_overwrites_only_when_explicitly_requested(self, rename_service, temp_dir):
        source_path = Path(temp_dir) / "source.mp4"
        source_path.write_bytes(b"new content")
        request = RenameRequest(
            source_path=str(source_path),
            title="Test Show",
            season=1,
            episode=1,
            output_dir=str(Path(temp_dir) / "output"),
            conflict_action="overwrite",
        )
        dest_path = Path(rename_service.preview_rename(request).dest_path)
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        dest_path.write_bytes(b"old content")

        result = rename_service.execute_rename(request)

        assert result.success is True
        assert Path(result.dest_path).read_bytes() == b"new content"

    def test_execute_rename_uses_numbered_name_when_requested(self, rename_service, temp_dir):
        source_path = Path(temp_dir) / "source.mp4"
        source_path.write_bytes(b"new content")
        request = RenameRequest(
            source_path=str(source_path),
            title="Test Show",
            season=1,
            episode=1,
            output_dir=str(Path(temp_dir) / "output"),
            conflict_action="rename",
        )
        original_dest = Path(rename_service.preview_rename(request).dest_path)
        original_dest.parent.mkdir(parents=True, exist_ok=True)
        original_dest.write_bytes(b"existing content")

        result = rename_service.execute_rename(request)

        assert result.success is True
        assert Path(result.dest_path).name == f"{original_dest.stem} (1){original_dest.suffix}"
        assert Path(result.dest_path).read_bytes() == b"new content"
        assert original_dest.read_bytes() == b"existing content"

    def test_execute_creates_directory_structure(self, rename_service, sample_video, temp_dir):
        """Test that execute creates necessary directories."""
        output_dir = Path(temp_dir) / "deep" / "nested" / "output"
        request = RenameRequest(
            source_path=sample_video,
            title="My Show",
            season=3,
            episode=10,
            output_dir=str(output_dir),
        )

        result = rename_service.execute_rename(request)

        assert result.success is True
        assert Path(result.dest_path).parent.exists()


class TestRenameServiceBatch:
    """Tests for batch_rename method."""

    def test_batch_rename_success(self, rename_service, temp_dir):
        """Test successful batch rename."""
        # Create multiple source files
        items = []
        for i in range(3):
            source_path = Path(temp_dir) / f"video{i}.mp4"
            source_path.write_bytes(b"video content")
            items.append(
                RenameRequest(
                    source_path=str(source_path),
                    title="Test Show",
                    season=1,
                    episode=i + 1,
                    output_dir=str(Path(temp_dir) / "output"),
                )
            )

        request = BatchRenameRequest(items=items)
        response = rename_service.batch_rename(request)

        assert response.total == 3
        assert response.success == 3
        assert response.failed == 0

    def test_batch_rename_dry_run(self, rename_service, temp_dir):
        """Test batch rename in dry-run mode."""
        # Create source files
        items = []
        for i in range(2):
            source_path = Path(temp_dir) / f"video{i}.mp4"
            source_path.write_bytes(b"video content")
            items.append(
                RenameRequest(
                    source_path=str(source_path),
                    title="Test Show",
                    season=1,
                    episode=i + 1,
                    output_dir=str(Path(temp_dir) / "output"),
                )
            )

        request = BatchRenameRequest(items=items, dry_run=True)
        response = rename_service.batch_rename(request)

        assert response.total == 2
        assert response.success == 2
        assert response.previews is not None
        assert len(response.previews) == 2
        # Original files should still exist (dry run)
        for item in items:
            assert Path(item.source_path).exists()

    def test_batch_rename_partial_failure(self, rename_service, temp_dir):
        """Test batch rename with some failures."""
        # Create one valid file
        valid_path = Path(temp_dir) / "valid.mp4"
        valid_path.write_bytes(b"video content")

        items = [
            RenameRequest(
                source_path=str(valid_path),
                title="Test Show",
                season=1,
                episode=1,
                output_dir=str(Path(temp_dir) / "output"),
            ),
            RenameRequest(
                source_path="/nonexistent/video.mp4",
                title="Test Show",
                season=1,
                episode=2,
                output_dir=str(Path(temp_dir) / "output"),
            ),
        ]

        request = BatchRenameRequest(items=items)
        response = rename_service.batch_rename(request)

        assert response.total == 2
        assert response.success == 1
        assert response.failed == 1

    def test_batch_rename_empty(self, rename_service):
        """Test batch rename with empty list."""
        request = BatchRenameRequest(items=[])
        response = rename_service.batch_rename(request)

        assert response.total == 0
        assert response.success == 0
        assert response.failed == 0


class TestRenameServiceHelpers:
    """Tests for helper methods."""

    def test_create_series_structure(self, rename_service, temp_dir):
        """Test creating series folder structure."""
        created = rename_service.create_series_structure(
            output_dir=temp_dir,
            title="My Test Show",
            seasons=[1, 2, 3],
        )

        assert len(created) == 4  # 1 series + 3 seasons
        assert Path(temp_dir) / "My Test Show" in [Path(d) for d in created]
        assert (Path(temp_dir) / "My Test Show" / "Season 01").exists()
        assert (Path(temp_dir) / "My Test Show" / "Season 02").exists()
        assert (Path(temp_dir) / "My Test Show" / "Season 03").exists()

    def test_create_series_structure_no_seasons(self, rename_service, temp_dir):
        """Test creating series structure without seasons."""
        created = rename_service.create_series_structure(
            output_dir=temp_dir,
            title="Another Show",
        )

        assert len(created) == 1
        assert (Path(temp_dir) / "Another Show").exists()

    def test_create_series_structure_sanitizes_title(self, rename_service, temp_dir):
        """Test that series structure sanitizes invalid characters."""
        created = rename_service.create_series_structure(
            output_dir=temp_dir,
            title="Show: With/Invalid?Chars",
        )

        # Should create directory without invalid characters
        assert len(created) == 1
        created_path = Path(created[0])
        assert created_path.exists()
        assert ":" not in created_path.name
        assert "/" not in created_path.name
        assert "?" not in created_path.name


class Test115OutputBranches:
    """Tests for provider-aware 115 output handling."""

    @staticmethod
    def _build_locator(
        *,
        provider: StorageProvider,
        path: str,
        file_id: str | None = None,
        parent_id: str | None = None,
        is_dir: bool,
    ) -> StorageLocator:
        return StorageLocator(
            provider=provider,
            path=path,
            file_id=file_id,
            parent_id=parent_id,
            is_dir=is_dir,
        )

    @pytest.mark.asyncio
    async def test_scraper_115_to_115_uses_provider_move(self, monkeypatch, temp_db):
        """115 source and 115 target should use provider-native move/copy path."""
        from server.services.scraper_service import ScraperService

        class FakeProvider:
            def __init__(self):
                self.ensure_calls = []
                self.rename_calls = []
                self.copy_calls = []

            async def ensure_directory(self, locator, relative_path):
                self.ensure_calls.append((locator, relative_path))
                return {"id": "season-dir", "parent_id": locator.file_id}

            async def rename(self, locator, target_name, target_parent_id):
                self.rename_calls.append((locator, target_name, target_parent_id))
                return {"state": True, "file_id": locator.file_id}

            async def copy(self, locator, target_name, target_parent_id):
                self.copy_calls.append((locator, target_name, target_parent_id))
                return {"state": True, "file_id": "copied-file-001"}

        provider = FakeProvider()

        service = ScraperService(
            config_service=ConfigService(db_path=temp_db),
            tmdb_service=None,
            parser_service=None,
            nfo_service=None,
            rename_service=RenameService(template_service=TemplateService(db_path=temp_db)),
            image_service=None,
            subtitle_service=None,
            emby_service=None,
        )
        monkeypatch.setattr(service, "_get_storage_provider", lambda provider_name: provider)

        file_locator = self._build_locator(
            provider=StorageProvider.P115,
            path="/115网盘/待整理/episode.mp4",
            file_id="file-001",
            parent_id="scan-root",
            is_dir=False,
        )
        output_locator = self._build_locator(
            provider=StorageProvider.P115,
            path="/115网盘/已整理",
            file_id="target-root",
            parent_id="0",
            is_dir=True,
        )

        result = await service._finalize_storage_output(
            file_locator=file_locator,
            output_locator=output_locator,
            metadata_locator=None,
            link_mode=OrganizeMode.MOVE,
            title="Test Show",
            season=1,
            episode=1,
            source_path=file_locator.path,
        )

        assert result.provider == StorageProvider.P115
        assert provider.ensure_calls
        assert provider.rename_calls
        assert not provider.copy_calls

    @pytest.mark.asyncio
    async def test_scraper_115_to_local_downloads_before_organize(
        self,
        monkeypatch,
        temp_db,
        tmp_path: Path,
    ):
        """115 source to local target should download the file before local organize."""
        from server.services.scraper_service import ScraperService

        downloaded_file = tmp_path / "downloaded.mp4"
        downloaded_file.write_bytes(b"downloaded")

        class FakeProvider:
            def __init__(self):
                self.download = AsyncMock(return_value=downloaded_file)

        provider = FakeProvider()

        service = ScraperService(
            config_service=ConfigService(db_path=temp_db),
            tmdb_service=None,
            parser_service=None,
            nfo_service=None,
            rename_service=RenameService(template_service=TemplateService(db_path=temp_db)),
            image_service=None,
            subtitle_service=None,
            emby_service=None,
        )
        monkeypatch.setattr(service, "_get_storage_provider", lambda provider_name: provider)

        file_locator = self._build_locator(
            provider=StorageProvider.P115,
            path="/115网盘/待整理/episode.mp4",
            file_id="file-001",
            parent_id="scan-root",
            is_dir=False,
        )
        output_locator = self._build_locator(
            provider=StorageProvider.LOCAL,
            path=str(tmp_path / "library"),
            is_dir=True,
        )

        result = await service._finalize_storage_output(
            file_locator=file_locator,
            output_locator=output_locator,
            metadata_locator=None,
            link_mode=OrganizeMode.COPY,
            title="Test Show",
            season=1,
            episode=1,
            source_path=file_locator.path,
        )

        provider.download.assert_awaited_once()
        assert result.provider == StorageProvider.LOCAL
        assert Path(result.path) == Path(output_locator.path)

    @pytest.mark.asyncio
    async def test_scrape_by_id_115_to_local_organizes_download_once(
        self,
        monkeypatch,
        temp_db,
        tmp_path: Path,
    ):
        """scrape_by_id for 115->local should download once and organize exactly once."""
        from server.services.scraper_service import ScraperService

        downloaded_file = tmp_path / "downloaded.mp4"
        downloaded_file.write_bytes(b"downloaded")

        class FakeProvider:
            def __init__(self):
                self.download = AsyncMock(return_value=downloaded_file)

        class FakeTMDBService:
            def __init__(self):
                self.get_series_by_api = AsyncMock(
                    return_value=TMDBSeries(
                        id=123,
                        name="Test Show",
                        original_name="Test Show",
                        first_air_date=date(2024, 1, 1),
                        number_of_seasons=1,
                        number_of_episodes=1,
                        seasons=[TMDBSeason(season_number=1, name="Season 1")],
                    )
                )
                self.get_season_by_api = AsyncMock(
                    return_value=TMDBSeason(
                        season_number=1,
                        name="Season 1",
                        episodes=[
                            TMDBEpisode(
                                episode_number=1,
                                name="Episode 1",
                            )
                        ],
                    )
                )

        provider = FakeProvider()
        tmdb_service = FakeTMDBService()

        service = ScraperService(
            config_service=ConfigService(db_path=temp_db),
            tmdb_service=tmdb_service,
            parser_service=None,
            nfo_service=None,
            rename_service=RenameService(template_service=TemplateService(db_path=temp_db)),
            image_service=None,
            subtitle_service=None,
            emby_service=None,
        )
        monkeypatch.setattr(service, "_get_storage_provider", lambda provider_name: provider)
        monkeypatch.setattr(service, "_generate_episode_nfo", lambda *args, **kwargs: "<episode />")
        monkeypatch.setattr(
            service,
            "_get_effective_nfo_config",
            AsyncMock(return_value={"nfo_enabled": False}),
        )
        monkeypatch.setattr(
            service,
            "_get_effective_download_config",
            AsyncMock(
                return_value={
                    "download_poster": False,
                    "download_fanart": False,
                    "download_thumb": False,
                }
            ),
        )
        monkeypatch.setattr(service, "_process_subtitles", lambda *args, **kwargs: [])

        file_locator = self._build_locator(
            provider=StorageProvider.P115,
            path="/115网盘/待整理/episode.mp4",
            file_id="file-001",
            parent_id="scan-root",
            is_dir=False,
        )
        output_locator = self._build_locator(
            provider=StorageProvider.LOCAL,
            path=str(tmp_path / "library"),
            is_dir=True,
        )

        result = await service.scrape_by_id(
            ScrapeByIdRequest(
                file_path=file_locator.path,
                tmdb_id=123,
                season=1,
                episode=1,
                file_locator=file_locator,
                output_locator=output_locator,
                allow_local_output=True,
                link_mode=OrganizeMode.COPY,
                # This test isolates 115 publication and intentionally has no
                # Emby service. Opt out explicitly now that checks fail closed.
                skip_emby_check=True,
            )
        )

        expected_folder = Path(output_locator.path) / "Test Show (2024)" / "Season 1"

        provider.download.assert_awaited_once()
        assert result.status == ScrapeStatus.SUCCESS
        assert result.dest_path is not None
        assert Path(result.dest_path).parent == expected_folder
        assert Path(result.dest_path).exists()


class TestProviderPublicationSafety:
    @pytest.mark.asyncio
    async def test_metadata_preparation_creates_new_destination_directories(
        self, tmp_path: Path
    ):
        from server.services.scraper_service import ScraperService

        nfo_service = Mock()
        nfo_service.tvshow_from_tmdb.return_value = Mock()
        nfo_service.generate_tvshow_nfo.return_value = "<tvshow />"
        nfo_service.generate_season_nfo.return_value = "<season />"
        service = ScraperService(
            config_service=Mock(),
            tmdb_service=None,
            parser_service=None,
            nfo_service=nfo_service,
            rename_service=Mock(),
            image_service=None,
            subtitle_service=None,
            emby_service=None,
        )
        service._get_effective_nfo_config = AsyncMock(
            return_value={"nfo_enabled": True}
        )
        service._get_effective_download_config = AsyncMock(
            return_value={
                "download_poster": False,
                "download_fanart": False,
                "download_thumb": False,
                "overwrite_existing": False,
            }
        )
        service._get_season_nfo_data = Mock(return_value=Mock())
        destination = (
            tmp_path / "library" / "Show" / "Season 1" / "Show S01E01.mkv"
        )

        nfo_path, _, season_folder = await service._write_local_metadata_only(
            title="Show",
            season=1,
            episode=1,
            year=None,
            metadata_dir=None,
            output_dir_for_preview=str(tmp_path / "library"),
            nfo_content="<episode />",
            series=SimpleNamespace(name="Show"),
            season_info=None,
            move_step=ScrapeLogStep(name="移动文件"),
            notify_log_update=AsyncMock(),
            link_mode=OrganizeMode.MOVE,
            advanced_settings=None,
            dest_path_override=destination,
            require_metadata_dir=False,
        )

        assert Path(nfo_path).read_text(encoding="utf-8") == "<episode />"
        assert (season_folder / "season.nfo").exists()
        assert (season_folder.parent / "tvshow.nfo").exists()

    @pytest.mark.asyncio
    async def test_copy_renames_only_the_new_115_file(self):
        from server.services.scraper_service import _P115StorageProvider

        client = Mock()
        client.fs_files = AsyncMock(
            side_effect=[
                {"data": [{"n": "episode.mkv", "fid": "existing"}]},
                {
                    "data": [
                        {"n": "episode.mkv", "fid": "existing"},
                        {"n": "episode.mkv", "fid": "new-copy"},
                    ]
                },
            ]
        )
        client.fs_copy = AsyncMock(return_value={"state": True})
        client.fs_rename = AsyncMock(return_value={"state": True})
        provider = _P115StorageProvider(Mock())
        provider._get_client = AsyncMock(return_value=(client, "web"))
        locator = StorageLocator(
            provider=StorageProvider.P115,
            path="/115网盘/incoming/episode.mkv",
            file_id="source-file",
            parent_id="incoming",
            is_dir=False,
        )

        result = await provider.copy(locator, "S01E01.mkv", "target")

        assert result["file_id"] == "new-copy"
        client.fs_rename.assert_awaited_once_with(
            ("new-copy", "S01E01.mkv"), async_=True
        )

    @pytest.mark.asyncio
    async def test_move_rename_failure_rolls_back_to_original_directory(self):
        from server.services.scraper_service import _P115StorageProvider

        client = Mock()
        client.fs_move = AsyncMock(
            side_effect=[{"state": True}, {"state": True}]
        )
        client.fs_rename = AsyncMock(
            return_value={"state": False, "message": "rename rejected"}
        )
        provider = _P115StorageProvider(Mock())
        provider._get_client = AsyncMock(return_value=(client, "web"))
        locator = StorageLocator(
            provider=StorageProvider.P115,
            path="/115网盘/incoming/episode.mkv",
            file_id="source-file",
            parent_id="incoming",
            is_dir=False,
        )

        with pytest.raises(ValueError, match="已移回原目录"):
            await provider.rename(locator, "S01E01.mkv", "target")

        assert client.fs_move.await_args_list[0].args == ("source-file",)
        assert client.fs_move.await_args_list[0].kwargs["pid"] == "target"
        assert client.fs_move.await_args_list[1].kwargs["pid"] == "incoming"

    @pytest.mark.asyncio
    async def test_metadata_failure_happens_before_local_media_publication(
        self, tmp_path: Path
    ):
        from server.services.scraper_service import ScraperService

        source = tmp_path / "source.mkv"
        source.write_bytes(b"video")
        destination = tmp_path / "library" / "Show" / "Season 1" / "S01E01.mkv"
        rename_service = Mock()
        rename_service.resolve_destination_path = Mock(return_value=destination)
        rename_service.execute_rename = Mock()
        service = ScraperService(
            config_service=Mock(),
            tmdb_service=None,
            parser_service=None,
            nfo_service=None,
            rename_service=rename_service,
            image_service=None,
            subtitle_service=None,
            emby_service=None,
        )
        service._write_local_metadata_only = AsyncMock(
            side_effect=OSError("metadata disk full")
        )
        request = RenameRequest(
            source_path=str(source),
            title="Show",
            season=1,
            episode=1,
            output_dir=str(tmp_path / "library"),
        )

        with pytest.raises(OSError, match="metadata disk full"):
            await service._prepare_and_organize_local_output(
                rename_request=request,
                source_display_path=str(source),
                output_dir_display=request.output_dir,
                title="Show",
                season=1,
                episode=1,
                year=None,
                metadata_dir=None,
                nfo_content="<episode />",
                series=Mock(),
                season_info=None,
                mode_name="移动",
                move_step=ScrapeLogStep(name="移动文件"),
                notify_log_update=AsyncMock(),
                result=ScrapeResult(
                    file_path=str(source),
                    status=ScrapeStatus.MOVE_FAILED,
                ),
                advanced_settings=None,
            )

        rename_service.execute_rename.assert_not_called()
        assert source.exists()

    @pytest.mark.asyncio
    async def test_post_publication_audit_failure_does_not_retry_media(self):
        from server.services.scraper_service import ScraperService

        service = ScraperService(
            config_service=Mock(),
            tmdb_service=None,
            parser_service=None,
            nfo_service=None,
            rename_service=Mock(),
            image_service=None,
            subtitle_service=None,
            emby_service=None,
        )
        service._record_media_version = AsyncMock(
            side_effect=OSError("database unavailable")
        )
        result = ScrapeResult(
            file_path="/media/source.mkv",
            dest_path="/library/S01E01.mkv",
            status=ScrapeStatus.MOVE_FAILED,
        )
        logs = [ScrapeLogStep(name="移动文件")]

        completed = await service._complete_scrape_output(
            result=result,
            file_path=result.file_path,
            tmdb_id=123,
            series=SimpleNamespace(name="Show"),
            season_info=None,
            season=1,
            episode=1,
            scrape_logs=logs,
            notify_log_update=AsyncMock(),
            remember_manual_alias=False,
            parsed_title=None,
        )

        assert completed.status == ScrapeStatus.SUCCESS
        assert "媒体版本记录写入失败" in logs[-1].logs[-1].message
