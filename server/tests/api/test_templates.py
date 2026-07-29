"""Unit tests for Templates API endpoints."""

import pytest
from fastapi.testclient import TestClient

from server.main import app


@pytest.fixture
def client(override_auth):
    """Provide a test client."""
    return TestClient(app)


class TestTemplatesAPI:
    """Tests for /api/templates endpoints."""

    def test_get_default_template(self, client):
        """Test getting default template."""
        response = client.get("/api/templates/default")

        assert response.status_code == 200
        data = response.json()
        assert "series_folder" in data
        assert "season_folder" in data
        assert "episode_file" in data
        assert data["series_folder"] == "{title} ({year})"

    def test_preview_valid_template(self, client):
        """Test previewing valid template."""
        response = client.post(
            "/api/templates/preview",
            json={
                "template": "{title} - S{season:02d}E{episode:02d}",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is True
        assert "权力的游戏" in data["preview"]
        assert "S01E01" in data["preview"]

    def test_preview_with_custom_data(self, client):
        """Test previewing with custom sample data."""
        response = client.post(
            "/api/templates/preview",
            json={
                "template": "{title} ({year})",
                "sample_data": {"title": "自定义剧名", "year": 2024},
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is True
        assert data["preview"] == "自定义剧名 (2024)"

    def test_preview_invalid_template(self, client):
        """Test previewing invalid template."""
        response = client.post(
            "/api/templates/preview",
            json={
                "template": "{invalid_variable}",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is False
        assert data["error"] is not None

    def test_validate_valid_template(self, client):
        """Test validating valid template."""
        response = client.post(
            "/api/templates/validate",
            json={
                "template": "{title} - S{season:02d}E{episode:02d} - {episode_title}",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is True
        assert "title" in data["variables_found"]
        assert "season" in data["variables_found"]
        assert "episode" in data["variables_found"]
        assert "episode_title" in data["variables_found"]

    def test_validate_empty_template(self, client):
        """Test validating empty template."""
        response = client.post(
            "/api/templates/validate",
            json={
                "template": "",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is False
        assert "empty" in data["error"].lower()

    def test_validate_invalid_variable(self, client):
        """Test validating template with invalid variable."""
        response = client.post(
            "/api/templates/validate",
            json={
                "template": "{title} - {not_a_real_var}",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is False
        assert "invalid" in data["error"].lower()

    def test_validate_no_variables(self, client):
        """Test validating template with no variables."""
        response = client.post(
            "/api/templates/validate",
            json={
                "template": "static text only",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is True
        assert data["variables_found"] == []
