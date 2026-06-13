"""Unit tests for ConfigService."""

import pytest
from pathlib import Path
import tempfile

from server.services.config_service import ConfigService
from server.models.template import NamingTemplate


@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        yield db_path


@pytest.fixture
def config_service(temp_db):
    """Provide a ConfigService instance with temp database."""
    return ConfigService(db_path=temp_db)


class TestConfigService:
    """Tests for ConfigService class."""

    @pytest.mark.asyncio
    async def test_set_and_get(self, config_service):
        """Test basic set and get operations."""
        await config_service.set("test_key", "test_value")
        result = await config_service.get("test_key")
        assert result == "test_value"

    @pytest.mark.asyncio
    async def test_get_nonexistent(self, config_service):
        """Test getting a nonexistent key."""
        result = await config_service.get("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_set_encrypted(self, config_service):
        """Test encrypted storage."""
        await config_service.set("secret", "my_secret_value", encrypted=True)

        # Get with decryption
        decrypted = await config_service.get("secret", encrypted=True)
        assert decrypted == "my_secret_value"

        # Get without decryption (should return encrypted value)
        encrypted = await config_service.get("secret", encrypted=False)
        assert encrypted != "my_secret_value"

    @pytest.mark.asyncio
    async def test_delete(self, config_service):
        """Test delete operation."""
        await config_service.set("to_delete", "value")
        assert await config_service.exists("to_delete")

        deleted = await config_service.delete("to_delete")
        assert deleted is True
        assert not await config_service.exists("to_delete")

    @pytest.mark.asyncio
    async def test_delete_nonexistent(self, config_service):
        """Test deleting a nonexistent key."""
        deleted = await config_service.delete("nonexistent")
        assert deleted is False

    @pytest.mark.asyncio
    async def test_exists(self, config_service):
        """Test exists check."""
        assert not await config_service.exists("new_key")
        await config_service.set("new_key", "value")
        assert await config_service.exists("new_key")

    @pytest.mark.asyncio
    async def test_update_existing(self, config_service):
        """Test updating an existing key."""
        await config_service.set("key", "value1")
        await config_service.set("key", "value2")
        result = await config_service.get("key")
        assert result == "value2"

    @pytest.mark.asyncio
    async def test_save_cookie(self, config_service):
        """Test cookie save operation."""
        await config_service.save_cookie("session_id=abc123")
        cookie = await config_service.get_cookie()
        assert cookie == "session_id=abc123"

    @pytest.mark.asyncio
    async def test_has_cookie(self, config_service):
        """Test has_cookie check."""
        assert not await config_service.has_cookie()
        await config_service.save_cookie("session_id=abc123")
        assert await config_service.has_cookie()

    @pytest.mark.asyncio
    async def test_delete_cookie(self, config_service):
        """Test cookie deletion."""
        await config_service.save_cookie("session_id=abc123")
        await config_service.set_cookie_verified(True)

        deleted = await config_service.delete_cookie()
        assert deleted is True
        assert not await config_service.has_cookie()

    @pytest.mark.asyncio
    async def test_cookie_verification_status(self, config_service):
        """Test cookie verification status tracking."""
        await config_service.set_cookie_verified(True)
        is_valid, verified_at = await config_service.get_cookie_verification()

        assert is_valid is True
        assert verified_at is not None

    @pytest.mark.asyncio
    async def test_cookie_verification_invalid(self, config_service):
        """Test invalid cookie verification status."""
        await config_service.set_cookie_verified(False)
        is_valid, verified_at = await config_service.get_cookie_verification()

        assert is_valid is False
        assert verified_at is not None

    @pytest.mark.asyncio
    async def test_save_and_get_naming_config(self, config_service):
        """Test saving and reading naming template configuration."""
        config = NamingTemplate(
            series_folder="{title}",
            season_folder="S{season:02d}",
            episode_file="{title}.S{season:02d}E{episode:02d}",
        )

        await config_service.save_naming_config(config)
        result = await config_service.get_naming_config()

        assert result == config
