"""Unit tests for config API endpoints.

测试 /api/config 路由。
使用 conftest.py 中的 override_auth fixture 绕过认证。
"""

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient

from server.main import app
from server.core.container import get_config_service, get_tmdb_service
from server.services.config_service import ConfigService
from server.services.tmdb_service import TMDBService


@pytest.fixture
def config_client(temp_db: Path, override_auth) -> TestClient:
    """
    提供带有配置服务和认证覆盖的测试客户端。

    Args:
        temp_db: Temporary database path.
        override_auth: Authentication override fixture.

    Returns:
        Configured test client.
    """
    config_service = ConfigService(db_path=temp_db)
    tmdb_service = TMDBService(config_service=config_service)

    def override_config_service():
        return config_service

    def override_tmdb_service():
        return tmdb_service

    app.dependency_overrides[get_config_service] = override_config_service
    app.dependency_overrides[get_tmdb_service] = override_tmdb_service
    yield TestClient(app)
    app.dependency_overrides.clear()


class TestProxyAPI:
    """Tests for /api/config/proxy endpoints."""

    def test_get_proxy_default(self, config_client):
        """Test getting default proxy configuration."""
        response = config_client.get("/api/config/proxy")

        assert response.status_code == 200
        data = response.json()
        assert "type" in data
        assert "host" in data
        assert "port" in data

    def test_save_proxy_config(self, config_client):
        """Test saving proxy configuration via PUT."""
        response = config_client.put(
            "/api/config/proxy",
            json={
                "type": "http",
                "host": "127.0.0.1",
                "port": 7890,
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["type"] == "http"
        assert data["host"] == "127.0.0.1"
        assert data["port"] == 7890

    def test_save_proxy_with_auth(self, config_client):
        """Test saving proxy configuration with authentication."""
        response = config_client.put(
            "/api/config/proxy",
            json={
                "type": "socks5",
                "host": "proxy.example.com",
                "port": 1080,
                "username": "user",
                "password": "pass",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["has_auth"] is True

    def test_delete_proxy_config(self, config_client):
        """Test deleting proxy configuration."""
        # First save a proxy
        config_client.put(
            "/api/config/proxy",
            json={"type": "http", "host": "127.0.0.1", "port": 7890},
        )

        # Then delete it
        response = config_client.delete("/api/config/proxy")

        assert response.status_code == 200

    def test_proxy_test_mocked_success(self, config_client):
        """Test proxy connection testing with mocked success."""
        with patch.object(
            TMDBService, "test_proxy", new_callable=AsyncMock
        ) as mock_test:
            mock_test.return_value = (True, "连接成功", 150)

            response = config_client.post("/api/config/proxy/test")

            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert data["latency_ms"] == 150

    def test_proxy_test_mocked_failure(self, config_client):
        """Test proxy connection testing with mocked failure."""
        with patch.object(
            TMDBService, "test_proxy", new_callable=AsyncMock
        ) as mock_test:
            mock_test.return_value = (False, "连接超时", None)

            response = config_client.post("/api/config/proxy/test")

            assert response.status_code == 200
            data = response.json()
            assert data["success"] is False

    def test_proxy_test_uses_request_config(self, config_client):
        """Test proxy connection uses the current request config instead of only saved config."""
        with patch.object(
            TMDBService, "test_proxy", new_callable=AsyncMock
        ) as mock_test:
            mock_test.return_value = (True, "连接成功", 88)

            response = config_client.post(
                "/api/config/proxy/test",
                json={
                    "type": "http",
                    "host": "127.0.0.1",
                    "port": 7890,
                },
            )

            assert response.status_code == 200
            mock_test.assert_awaited_once_with("http://127.0.0.1:7890")


class TestAPITokenAPI:
    """Tests for /api/config/api-token endpoints."""

    def test_get_api_token_status_not_configured(self, config_client):
        """Test getting status when no API token is configured."""
        response = config_client.get("/api/config/api-token/status")

        assert response.status_code == 200
        data = response.json()
        assert data["is_configured"] is False

    def test_save_api_token_mocked_success(self, config_client):
        """Test saving API token with mocked verification."""
        with patch.object(
            TMDBService, "verify_api_token", new_callable=AsyncMock
        ) as mock_verify:
            mock_verify.return_value = (True, None)

            response = config_client.post(
                "/api/config/api-token",
                json={"token": "valid_api_token_12345"},
            )

            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True

    def test_save_api_token_invalid(self, config_client):
        """Test saving API token with mocked failed verification."""
        with patch.object(
            TMDBService, "verify_api_token", new_callable=AsyncMock
        ) as mock_verify:
            mock_verify.return_value = (False, "Invalid API token")

            response = config_client.post(
                "/api/config/api-token",
                json={"token": "invalid_token"},
            )

            assert response.status_code == 200
            data = response.json()
            assert data["success"] is False

    def test_save_api_token_empty(self, config_client):
        """Test saving empty API token."""
        response = config_client.post(
            "/api/config/api-token",
            json={"token": ""},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False

    def test_delete_api_token_not_configured(self, config_client):
        """Test deleting when no API token is configured."""
        response = config_client.delete("/api/config/api-token")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False

    def test_api_token_missing_field(self, config_client):
        """Test 422 response for missing token field."""
        response = config_client.post("/api/config/api-token", json={})

        assert response.status_code == 422


class TestLanguageAPI:
    """Tests for /api/config/language endpoints."""

    def test_get_language_default(self, config_client):
        """Test getting default language configuration."""
        response = config_client.get("/api/config/language")

        assert response.status_code == 200
        data = response.json()
        assert "primary" in data

    def test_save_language_config(self, config_client):
        """Test saving language configuration."""
        response = config_client.put(
            "/api/config/language",
            json={"primary": "en-US"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["primary"] == "en-US"


class TestOrganizeAPI:
    """Tests for /api/config/organize endpoints."""

    def test_get_organize_default(self, config_client):
        """Test getting default organize configuration."""
        response = config_client.get("/api/config/organize")

        assert response.status_code == 200
        data = response.json()
        # 检查实际存在的字段
        assert "organize_mode" in data or "organize_dir" in data


class TestNamingAPI:
    """Tests for /api/config/naming endpoints."""

    def test_get_naming_default(self, config_client):
        """Test getting default naming configuration."""
        response = config_client.get("/api/config/naming")

        assert response.status_code == 200
        data = response.json()
        assert data["series_folder"] == "{title} ({year})"
        assert data["season_folder"] == "Season {season}"

    def test_save_naming_config(self, config_client):
        """Test saving naming configuration."""
        payload = {
            "series_folder": "{title}",
            "season_folder": "S{season:02d}",
            "episode_file": "{title}.S{season:02d}E{episode:02d}",
        }

        save_response = config_client.put("/api/config/naming", json=payload)
        get_response = config_client.get("/api/config/naming")

        assert save_response.status_code == 200
        assert save_response.json() == payload
        assert get_response.status_code == 200
        assert get_response.json() == payload


class TestNfoAPI:
    """Tests for /api/config/nfo endpoints."""

    def test_get_nfo_default(self, config_client):
        """Test getting default NFO configuration."""
        response = config_client.get("/api/config/nfo")

        assert response.status_code == 200


class TestSystemAPI:
    """Tests for /api/config/system endpoints."""

    def test_get_system_default(self, config_client):
        """Test getting default system configuration."""
        response = config_client.get("/api/config/system")

        assert response.status_code == 200
