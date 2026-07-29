"""Regression tests for scrape/manual job API status codes."""

from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from server.api.manual_job import get_service as get_manual_job_service
from server.api.scrape_job import get_service as get_scrape_job_service
from server.main import app


def test_duplicate_scrape_job_returns_conflict(auth_client: TestClient) -> None:
    """A duplicate is a business conflict, not a response-validation 500."""
    service = AsyncMock()
    service.create_job.return_value = None
    app.dependency_overrides[get_scrape_job_service] = lambda: service
    try:
        response = auth_client.post(
            "/api/scrape-jobs",
            json={
                "file_path": "/incoming/episode.mp4",
                "output_dir": "/library",
                "source": "manual",
            },
        )
    finally:
        app.dependency_overrides.pop(get_scrape_job_service, None)

    assert response.status_code == 409
    assert response.json()["detail"] == "该文件已有待处理任务"


def test_missing_scrape_job_returns_not_found(auth_client: TestClient) -> None:
    """Missing scrape job IDs produce a stable 404."""
    service = AsyncMock()
    service.get_job.return_value = None
    app.dependency_overrides[get_scrape_job_service] = lambda: service
    try:
        response = auth_client.get("/api/scrape-jobs/missing")
    finally:
        app.dependency_overrides.pop(get_scrape_job_service, None)
    assert response.status_code == 404


def test_missing_manual_job_returns_not_found(auth_client: TestClient) -> None:
    """Missing manual job IDs produce a stable 404."""
    service = AsyncMock()
    service.get_job.return_value = None
    app.dependency_overrides[get_manual_job_service] = lambda: service
    try:
        response = auth_client.get("/api/manual-jobs/999999")
    finally:
        app.dependency_overrides.pop(get_manual_job_service, None)
    assert response.status_code == 404
