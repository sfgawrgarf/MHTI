"""Regression tests for scrape/manual job API status codes."""

from types import SimpleNamespace
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


def test_cancel_scrape_job_returns_terminal_status(auth_client: TestClient) -> None:
    service = AsyncMock()
    service.cancel_job.return_value = (
        SimpleNamespace(status=SimpleNamespace(value="cancelled")),
        True,
        "任务已取消",
    )
    app.dependency_overrides[get_scrape_job_service] = lambda: service
    try:
        response = auth_client.post("/api/scrape-jobs/job-1/cancel")
    finally:
        app.dependency_overrides.pop(get_scrape_job_service, None)

    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    service.cancel_job.assert_awaited_once_with("job-1")


def test_cancel_manual_job_reports_cancelled_children(auth_client: TestClient) -> None:
    service = AsyncMock()
    service.cancel_job.return_value = (
        SimpleNamespace(status=SimpleNamespace(value="cancelled")),
        True,
        3,
        "任务已取消",
    )
    app.dependency_overrides[get_manual_job_service] = lambda: service
    try:
        response = auth_client.post("/api/manual-jobs/7/cancel")
    finally:
        app.dependency_overrides.pop(get_manual_job_service, None)

    assert response.status_code == 200
    assert response.json()["cancelled_scrape_jobs"] == 3
    service.cancel_job.assert_awaited_once_with(7)
