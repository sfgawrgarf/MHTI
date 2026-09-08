"""Unit tests for ImageService."""

import asyncio

import pytest
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

import httpx

from server.services.image_service import ImageService
from server.models.image import ImageDownloadRequest, ImageSize
from server.models.system import SystemConfig
from server.models.config import ProxyConfig


@pytest.fixture
def mock_config_service():
    """Provide a mock ConfigService."""
    config_service = MagicMock()
    config_service.get_system_config = AsyncMock(return_value=SystemConfig(
        retry_count=3,
        concurrent_downloads=3,
        task_timeout=30
    ))
    config_service.get_proxy_config = AsyncMock(return_value=ProxyConfig())
    return config_service


@pytest.fixture
def image_service(mock_config_service):
    """Provide an ImageService instance."""
    return ImageService(config_service=mock_config_service)


@pytest.fixture
def temp_dir():
    """Create a temporary directory for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


class TestImageServiceBasic:
    """Basic tests for ImageService."""

    def test_get_full_image_url(self, image_service):
        """Test full URL generation."""
        url = image_service.get_full_image_url("/abc123.jpg", ImageSize.W500)
        assert url == "https://image.tmdb.org/t/p/w500/abc123.jpg"

    def test_get_full_image_url_original(self, image_service):
        """Test full URL with original size."""
        url = image_service.get_full_image_url("/poster.jpg", ImageSize.ORIGINAL)
        assert url == "https://image.tmdb.org/t/p/original/poster.jpg"

    def test_get_full_image_url_none(self, image_service):
        """Test URL generation with None path."""
        url = image_service.get_full_image_url(None)
        assert url is None

    def test_get_full_image_url_empty(self, image_service):
        """Test URL generation with empty path."""
        url = image_service.get_full_image_url("")
        assert url is None


@pytest.fixture
def install_transport(monkeypatch):
    client_type = httpx.AsyncClient

    def install(handler):
        monkeypatch.setattr(
            httpx, "AsyncClient",
            lambda **kwargs: client_type(transport=httpx.MockTransport(handler), **kwargs),
        )
    return install


class ImageStream(httpx.AsyncByteStream):
    def __init__(self, chunks, error=None):
        self.chunks = chunks
        self.error = error
        self.consumed = 0
        self.closed = False

    async def __aiter__(self):
        for chunk in self.chunks:
            self.consumed += 1
            yield chunk
        if self.error:
            raise self.error

    async def aclose(self):
        self.closed = True


class TestImageServiceDownload:
    @pytest.mark.asyncio
    async def test_success_replaces_complete_file(self, image_service, temp_dir, install_transport):
        target = Path(temp_dir) / "poster.jpg"
        target.write_bytes(b"old")
        target.chmod(0o640)
        stream = ImageStream([b"a" * 65536, b"tail"])
        install_transport(lambda request: httpx.Response(
            200, headers={"content-type": "image/jpeg"}, stream=stream,
        ))
        result = await image_service.download_image("https://example.com/image.jpg", temp_dir, target.name)
        assert result.success
        assert target.read_bytes() == b"a" * 65536 + b"tail"
        assert target.stat().st_mode & 0o777 == 0o640
        assert stream.closed
        assert not list(Path(temp_dir).glob("*.part"))

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status,content_type,error", [
        (404, "image/jpeg", "404"),
        (200, "text/html", "not an image"),
    ])
    async def test_rejected_headers_do_not_read_body(
        self, image_service, temp_dir, install_transport, status, content_type, error,
    ):
        stream = ImageStream([b"unwanted"])
        install_transport(lambda request: httpx.Response(
            status, headers={"content-type": content_type}, stream=stream,
        ))
        result = await image_service.download_image("https://example.com/image.jpg", temp_dir, "poster.jpg")
        assert not result.success and error in result.error
        assert stream.consumed == 0
        assert stream.closed
        assert not list(Path(temp_dir).iterdir())

    @pytest.mark.asyncio
    @pytest.mark.parametrize("advertised", [None, "1", "invalid", "99999999"])
    async def test_size_limit_preserves_original(
        self, image_service, temp_dir, install_transport, monkeypatch, advertised,
    ):
        monkeypatch.setattr("server.services.image_service.MAX_IMAGE_BYTES", 65536)
        target = Path(temp_dir) / "poster.jpg"
        target.write_bytes(b"original")
        stream = ImageStream([b"a" * 65536] * 4)
        headers = {"content-type": "image/jpeg"}
        if advertised is not None:
            headers["content-length"] = advertised
        install_transport(lambda request: httpx.Response(200, headers=headers, stream=stream))
        result = await image_service.download_image("https://example.com/image.jpg", temp_dir, target.name)
        assert not result.success and "20 MB" in result.error
        assert stream.consumed <= 2
        assert stream.closed
        assert target.read_bytes() == b"original"
        assert sorted(p.name for p in Path(temp_dir).iterdir()) == ["poster.jpg"]

    @pytest.mark.asyncio
    async def test_midstream_timeout_retries_without_partial_file(
        self, image_service, temp_dir, install_transport, mock_config_service,
    ):
        mock_config_service.get_system_config.return_value = SystemConfig(retry_count=2)
        target = Path(temp_dir) / "poster.jpg"
        target.write_bytes(b"original")
        streams = [
            ImageStream([b"x" * 65536], httpx.ReadTimeout("timeout")),
            ImageStream([b"complete"]),
        ]
        attempts = 0

        def handler(request):
            nonlocal attempts
            assert target.read_bytes() == b"original"
            assert not list(Path(temp_dir).glob("*.part"))
            stream = streams[attempts]
            attempts += 1
            return httpx.Response(200, headers={"content-type": "image/jpeg"}, stream=stream)

        install_transport(handler)
        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await image_service.download_image("https://example.com/image.jpg", temp_dir, target.name)
        assert result.success and attempts == 2
        assert target.read_bytes() == b"complete"
        assert all(stream.closed for stream in streams)

    @pytest.mark.asyncio
    async def test_cancelled_stream_cleans_up(self, image_service, temp_dir, install_transport):
        target = Path(temp_dir) / "poster.jpg"
        target.write_bytes(b"original")
        stream = ImageStream([b"x" * 65536], asyncio.CancelledError())
        install_transport(lambda request: httpx.Response(
            200, headers={"content-type": "image/jpeg"}, stream=stream,
        ))
        with pytest.raises(asyncio.CancelledError):
            await image_service.download_image("https://example.com/image.jpg", temp_dir, target.name)
        assert stream.closed
        assert target.read_bytes() == b"original"
        assert sorted(p.name for p in Path(temp_dir).iterdir()) == ["poster.jpg"]

    @pytest.mark.asyncio
    async def test_write_failure_preserves_original(self, image_service, temp_dir, install_transport, monkeypatch):
        target = Path(temp_dir) / "poster.jpg"
        target.write_bytes(b"original")
        install_transport(lambda request: httpx.Response(
            200, headers={"content-type": "image/jpeg"}, stream=ImageStream([b"new"]),
        ))

        def fail_replace(*args):
            raise OSError("disk error")

        monkeypatch.setattr("server.services.image_service.os.replace", fail_replace)
        result = await image_service.download_image("https://example.com/image.jpg", temp_dir, target.name)
        assert not result.success and "File system error" in result.error
        assert target.read_bytes() == b"original"
        assert sorted(p.name for p in Path(temp_dir).iterdir()) == ["poster.jpg"]

    @pytest.mark.asyncio
    async def test_connection_error(self, image_service, temp_dir, install_transport, mock_config_service):
        mock_config_service.get_system_config.return_value = SystemConfig(retry_count=1)

        def fail(request):
            raise httpx.ConnectError("connection failed")
        install_transport(fail)
        result = await image_service.download_image("https://example.com/image.jpg", temp_dir, "poster.jpg")
        assert not result.success and "Connection error" in result.error


class TestImageServiceBatch:
    @pytest.mark.asyncio
    async def test_empty(self, image_service):
        result = await image_service.download_batch([])
        assert result.total == result.success == result.failed == 0
        assert result.results == []

    @pytest.mark.asyncio
    async def test_partial_failure(self, image_service, temp_dir, install_transport):
        def handler(request):
            if request.url.path.endswith("2.jpg"):
                return httpx.Response(404)
            return httpx.Response(
                200, headers={"content-type": "image/jpeg"}, stream=ImageStream([b"image"]),
            )
        install_transport(handler)
        requests = [
            ImageDownloadRequest(url=f"https://example.com/{i}.jpg", save_path=temp_dir, filename=f"{i}.jpg")
            for i in range(3)
        ]
        result = await image_service.download_batch(requests, concurrency=2)
        assert (result.total, result.success, result.failed) == (3, 2, 1)


class TestImageServiceHelpers:
    """Tests for helper methods."""

    def test_generate_series_image_requests(self, image_service):
        """Test series image request generation."""
        requests = image_service.generate_series_image_requests(
            save_path="/path/to/show",
            poster_path="/poster123.jpg",
            backdrop_path="/backdrop456.jpg",
        )

        assert len(requests) == 2
        assert requests[0].filename == "poster.jpg"
        assert requests[1].filename == "backdrop.jpg"
        assert "w500" in requests[0].url
        assert "w780" in requests[1].url  # Backdrop uses larger size

    def test_generate_series_image_requests_no_backdrop(self, image_service):
        """Test series image request with only poster."""
        requests = image_service.generate_series_image_requests(
            save_path="/path/to/show",
            poster_path="/poster123.jpg",
        )

        assert len(requests) == 1
        assert requests[0].filename == "poster.jpg"

    def test_generate_series_image_requests_none(self, image_service):
        """Test series image request with no images."""
        requests = image_service.generate_series_image_requests(
            save_path="/path/to/show",
        )

        assert len(requests) == 0

    def test_generate_season_image_request(self, image_service):
        """Test season image request generation."""
        request = image_service.generate_season_image_request(
            save_path="/path/to/show",
            season_number=1,
            poster_path="/season1.jpg",
        )

        assert request is not None
        assert request.filename == "season01-poster.jpg"
        assert "w500" in request.url

    def test_generate_season_image_request_none(self, image_service):
        """Test season image request with no poster."""
        request = image_service.generate_season_image_request(
            save_path="/path/to/show",
            season_number=1,
            poster_path=None,
        )

        assert request is None

    def test_generate_episode_image_request(self, image_service):
        """Test episode image request generation."""
        request = image_service.generate_episode_image_request(
            save_path="/path/to/show/Season 01",
            season_number=1,
            episode_number=5,
            still_path="/still105.jpg",
        )

        assert request is not None
        assert request.filename == "S01E05.jpg"
        assert "w500" in request.url

    def test_generate_episode_image_request_none(self, image_service):
        """Test episode image request with no still."""
        request = image_service.generate_episode_image_request(
            save_path="/path/to/show/Season 01",
            season_number=1,
            episode_number=5,
            still_path=None,
        )

        assert request is None
