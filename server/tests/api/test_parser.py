"""Unit tests for parser API endpoints."""

import pytest
from fastapi.testclient import TestClient

from server.main import app


@pytest.fixture
def client():
    """Provide a test client for the FastAPI app."""
    return TestClient(app)


class TestParserAPI:
    """Tests for /api/parse endpoints."""

    def test_parse_single_success(self, client):
        """Test successful single filename parsing."""
        response = client.post(
            "/api/parse",
            json={"filename": "Breaking.Bad.S01E01.720p.mp4"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["result"]["series_name"] == "Breaking Bad"
        assert data["result"]["season"] == 1
        assert data["result"]["episode"] == 1
        assert data["result"]["is_parsed"] is True

    def test_parse_with_filepath(self, client):
        """File paths are accepted as context but do not infer seasons by themselves."""
        response = client.post(
            "/api/parse",
            json={
                "filename": "E01.mp4",
                "filepath": "/media/TV/Friends/Season 1/E01.mp4",
            },
        )

        assert response.status_code == 200
        data = response.json()
        # The parser deliberately derives metadata from the filename only. A
        # directory named "Season 1" and an ambiguous bare "E01" filename
        # must not silently produce season or episode metadata.
        assert data["result"]["season"] is None
        assert data["result"]["episode"] is None

    def test_parse_chinese_format(self, client):
        """Test parsing Chinese format filename."""
        response = client.post(
            "/api/parse",
            json={"filename": "绝命毒师 第1季 第01集.mp4"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["result"]["series_name"] == "绝命毒师"
        assert data["result"]["season"] == 1
        assert data["result"]["episode"] == 1

    def test_parse_batch_success(self, client):
        """Test successful batch parsing."""
        response = client.post(
            "/api/parse/batch",
            json={
                "files": [
                    {"filename": "Breaking.Bad.S01E01.mp4"},
                    {"filename": "Game.of.Thrones.S08E06.mp4"},
                    {"filename": "The.Office.S02E03.mp4"},
                ]
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data["results"]) == 3
        assert data["success_rate"] == 1.0

    def test_parse_batch_partial_success(self, client):
        """Test batch parsing with some failures."""
        response = client.post(
            "/api/parse/batch",
            json={
                "files": [
                    {"filename": "Breaking.Bad.S01E01.mp4"},
                    {"filename": "random_file.mp4"},
                ]
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data["results"]) == 2
        assert 0.0 <= data["success_rate"] <= 1.0

    def test_parse_batch_empty(self, client):
        """Test batch parsing with empty list."""
        response = client.post(
            "/api/parse/batch",
            json={"files": []},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["results"] == []
        assert data["success_rate"] == 0.0

    def test_parse_missing_filename(self, client):
        """Test 422 response for missing filename."""
        response = client.post("/api/parse", json={})

        assert response.status_code == 422

    def test_parse_returns_original_filename(self, client):
        """Test that original filename is always returned."""
        filename = "Some.Random.File.mp4"
        response = client.post(
            "/api/parse",
            json={"filename": filename},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["result"]["original_filename"] == filename
