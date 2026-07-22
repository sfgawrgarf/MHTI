"""Unit tests for files API endpoints.

测试 /api/scan 和 /api/files 路由。
使用 conftest.py 中的 override_auth fixture 绕过认证。
"""

import pytest
from fastapi.testclient import TestClient

from server.core.container import get_file_service, get_history_service
from server.models.file import DirectoryEntry
from server.main import app


@pytest.fixture
def files_client(override_auth) -> TestClient:
    """
    提供带认证覆盖的测试客户端。

    Args:
        override_auth: Authentication override fixture.

    Returns:
        Configured test client.
    """
    class StubHistoryService:
        """Keep scan tests independent from the application's persistent database."""

        async def get_existing_fingerprints(self, fingerprints):
            return set()

    app.dependency_overrides[get_history_service] = lambda: StubHistoryService()
    yield TestClient(app)
    app.dependency_overrides.clear()


class TestFilesAPI:
    """Tests for /api/scan endpoint."""

    def test_scan_folder_success(self, files_client, tmp_path):
        """Test successful folder scan."""
        # Create test video files
        (tmp_path / "video1.mp4").touch()
        (tmp_path / "video2.mkv").touch()

        response = files_client.post("/api/scan", json={"folder_path": str(tmp_path)})

        assert response.status_code == 200
        data = response.json()
        assert data["folder_path"] == str(tmp_path)
        assert data["total_files"] == 2
        assert len(data["files"]) == 2

    def test_scan_folder_empty(self, files_client, tmp_path):
        """Test scanning empty folder."""
        response = files_client.post("/api/scan", json={"folder_path": str(tmp_path)})

        assert response.status_code == 200
        data = response.json()
        assert data["total_files"] == 0
        assert data["files"] == []

    def test_scan_folder_not_found(self, files_client):
        """Test 400 response for non-existent folder."""
        response = files_client.post(
            "/api/scan",
            json={"folder_path": "/nonexistent/path/that/does/not/exist"},
        )

        # 应返回 400（使用 AppException）
        assert response.status_code == 400
        data = response.json()
        assert "error" in data

    def test_scan_folder_invalid_path(self, files_client, tmp_path):
        """Test 400 response when path is not a directory."""
        file_path = tmp_path / "file.txt"
        file_path.touch()

        response = files_client.post("/api/scan", json={"folder_path": str(file_path)})

        assert response.status_code == 400

    def test_scan_folder_missing_path_returns_validation_error(self, files_client):
        """Missing folder_path should fail instead of silently scanning an empty path."""

        class SpyFileService:
            def __init__(self):
                self.calls = []

            def scan_folder(self, folder_path: str):
                self.calls.append(folder_path)
                return []

        class StubHistoryService:
            async def get_existing_fingerprints(self, fingerprints):
                return set()

        spy_service = SpyFileService()
        app.dependency_overrides[get_file_service] = lambda: spy_service
        app.dependency_overrides[get_history_service] = lambda: StubHistoryService()

        response = files_client.post("/api/scan", json={})

        assert response.status_code == 422
        assert spy_service.calls == []
        data = response.json()
        assert data["error"]["code"] == "E1001"
        assert data["error"]["details"]["field"] == "folder_path"

    def test_scan_folder_locator_115_delegates_to_provider_scan(self, files_client):
        """115 locator scans should delegate to scan_folder_async and return results."""
        from server.models.file import ScannedFile

        class SpyFileService:
            def __init__(self):
                self.async_calls = []
                self.calls = []

            def scan_folder(self, folder_path: str):
                self.calls.append(folder_path)
                return []

            async def scan_folder_async(self, folder_path: str, locator=None):
                self.async_calls.append((folder_path, locator))
                return [
                    ScannedFile(
                        filename="S01E01.mkv",
                        path="/115网盘/剧集/S01E01.mkv",
                        size=100,
                        extension=".mkv",
                        file_id="300",
                        parent_id="100",
                    )
                ]

        class StubHistoryService:
            async def get_existing_fingerprints(self, fingerprints):
                return set()

        spy_service = SpyFileService()
        app.dependency_overrides[get_file_service] = lambda: spy_service
        app.dependency_overrides[get_history_service] = lambda: StubHistoryService()

        response = files_client.post(
            "/api/scan",
            json={
                "locator": {
                    "provider": "115",
                    "path": "/115网盘",
                    "file_id": "0",
                }
            },
        )

        assert response.status_code == 200
        # Provider scans must go through the async path, not the local sync path.
        assert spy_service.calls == []
        assert len(spy_service.async_calls) == 1
        called_path, called_locator = spy_service.async_calls[0]
        assert called_path == "/115网盘"
        assert called_locator is not None
        assert called_locator.provider.value == "115"
        data = response.json()
        assert data["total_files"] == 1
        assert data["files"][0]["path"] == "/115网盘/剧集/S01E01.mkv"

    def test_scan_folder_with_video_extensions(self, files_client, tmp_path):
        """Test that only video files are returned."""
        # Create various files
        (tmp_path / "video.mp4").touch()
        (tmp_path / "video.mkv").touch()
        (tmp_path / "video.avi").touch()
        (tmp_path / "document.txt").touch()
        (tmp_path / "image.jpg").touch()

        response = files_client.post("/api/scan", json={"folder_path": str(tmp_path)})

        assert response.status_code == 200
        data = response.json()
        # 只应返回视频文件
        assert data["total_files"] == 3


class TestHealthCheck:
    """Tests for health check endpoint."""

    def test_health_check_reports_tmdb_configuration(self, monkeypatch):
        """Health check reads TMDB credentials from ConfigService's public API."""
        class StubConfigService:
            async def get_cookie(self):
                return "tmdb-cookie"

            async def get_api_token(self):
                return "tmdb-token"

        from server.core import container

        monkeypatch.setattr(container, "get_config_service", lambda: StubConfigService())

        client = TestClient(app)
        response = client.get("/health")

        assert response.status_code == 200
        assert response.json()["checks"]["tmdb_configured"] == "configured"

    def test_health_check(self):
        """Test health check endpoint (no auth required)."""
        # Health check 不需要认证
        client = TestClient(app)
        response = client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        # 可能包含 checks 字段
        if "checks" in data:
            assert "database" in data["checks"]


class TestFileBrowseAPI:
    """Tests for /api/files/browse endpoint."""

    def test_browse_folder_success(self, files_client, tmp_path):
        """Test successful folder browsing."""
        # Create test files and folders
        (tmp_path / "subfolder").mkdir()
        (tmp_path / "file.txt").touch()

        response = files_client.get(f"/api/files/browse?path={tmp_path}")

        assert response.status_code == 200
        data = response.json()
        assert "entries" in data
        assert len(data["entries"]) == 2

    def test_browse_folder_not_found(self, files_client):
        """Test browsing non-existent folder."""
        response = files_client.get("/api/files/browse?path=/nonexistent/folder/path")

        assert response.status_code == 400

    def test_browse_missing_path(self, files_client):
        """Test browsing without path parameter returns root or error."""
        response = files_client.get("/api/files/browse")

        # 应返回默认路径结果或 422
        assert response.status_code in [200, 422]

    def test_browse_root_contains_115_virtual_entry(self, files_client):
        """Default root browse should expose the virtual 115 entry."""
        response = files_client.get("/api/files/browse?page_size=100")

        assert response.status_code == 200
        data = response.json()
        virtual_entry = next(
            entry for entry in data["entries"] if entry["name"] == "115网盘"
        )
        assert virtual_entry["path"] == "/115网盘"
        assert virtual_entry["provider"] == "115"
        assert virtual_entry["file_id"] == "0"
        assert virtual_entry["is_virtual"] is True

    def test_browse_provider_params_are_forwarded(self, files_client):
        """Browse API should forward provider/file_id params to the async 115 browse path."""

        class SpyFileService:
            def __init__(self):
                self.calls = []

            def browse_directory(
                self,
                path: str = "",
                page: int = 1,
                page_size: int = 20,
                provider: str = "local",
                file_id: str | None = None,
            ):
                raise AssertionError("provider=115 should use browse_directory_async")

            async def browse_directory_async(
                self,
                path: str = "",
                page: int = 1,
                page_size: int = 20,
                provider: str = "local",
                file_id: str | None = None,
            ):
                self.calls.append(
                    {
                        "path": path,
                        "page": page,
                        "page_size": page_size,
                        "provider": provider,
                        "file_id": file_id,
                    }
                )
                return (
                    "/115网盘",
                    None,
                    [
                        DirectoryEntry(
                            name="影视库",
                            path="/115网盘/影视库",
                            is_dir=True,
                            provider="115",
                            file_id="200",
                            parent_id="0",
                            is_virtual=False,
                        )
                    ],
                    1,
                    "0",
                    None,
                )

        spy_service = SpyFileService()
        app.dependency_overrides[get_file_service] = lambda: spy_service

        response = files_client.get(
            "/api/files/browse",
            params={
                "path": "/115网盘",
                "provider": "115",
                "file_id": "0",
                "page": 2,
                "page_size": 10,
            },
        )

        assert response.status_code == 200
        assert spy_service.calls == [
            {
                "path": "/115网盘",
                "page": 2,
                "page_size": 10,
                "provider": "115",
                "file_id": "0",
            }
        ]
        data = response.json()
        assert data["entries"][0]["provider"] == "115"
        assert data["entries"][0]["file_id"] == "200"

    def test_browse_provider_115_allows_path_without_file_id(self, files_client):
        """115 browse should preserve a virtual subpath even when file_id is omitted."""

        class SpyFileService:
            def __init__(self):
                self.calls = []

            def browse_directory(self, *args, **kwargs):
                raise AssertionError("provider=115 should use browse_directory_async")

            async def browse_directory_async(
                self,
                path: str = "",
                page: int = 1,
                page_size: int = 20,
                provider: str = "local",
                file_id: str | None = None,
            ):
                self.calls.append(
                    {
                        "path": path,
                        "page": page,
                        "page_size": page_size,
                        "provider": provider,
                        "file_id": file_id,
                    }
                )
                return (
                    "/115网盘/电影",
                    "/115网盘",
                    [],
                    0,
                    "100",
                    "0",
                )

        spy_service = SpyFileService()
        app.dependency_overrides[get_file_service] = lambda: spy_service

        response = files_client.get(
            "/api/files/browse",
            params={
                "path": "/115网盘/电影",
                "provider": "115",
                "page": 1,
                "page_size": 10,
            },
        )

        assert response.status_code == 200
        assert spy_service.calls == [
            {
                "path": "/115网盘/电影",
                "page": 1,
                "page_size": 10,
                "provider": "115",
                "file_id": None,
            }
        ]
        data = response.json()
        assert data["current_path"] == "/115网盘/电影"
        assert data["parent_path"] == "/115网盘"
