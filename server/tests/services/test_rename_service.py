"""Unit tests for RenameService."""

import pytest
import tempfile
from pathlib import Path

from server.models.template import NamingTemplate
from server.services.config_service import ConfigService
from server.services.rename_service import RenameService
from server.services.template_service import TemplateService
from server.models.rename import RenameRequest, BatchRenameRequest


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
