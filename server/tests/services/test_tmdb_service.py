"""Unit tests for TMDBService."""

import pytest
from pathlib import Path
import tempfile
from unittest.mock import AsyncMock, patch, MagicMock

from server.services.tmdb_service import TMDBService
from server.services.config_service import ConfigService


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


@pytest.fixture
def tmdb_service(config_service):
    """Provide a TMDBService instance."""
    return TMDBService(config_service=config_service)


class TestTMDBService:
    """Tests for TMDBService class."""

    def test_get_image_url(self, tmdb_service):
        """Test image URL generation."""
        url = tmdb_service.get_image_url("/abc123.jpg", "w500")
        assert url == "https://image.tmdb.org/t/p/w500/abc123.jpg"

    def test_get_image_url_none(self, tmdb_service):
        """Test image URL with None path."""
        url = tmdb_service.get_image_url(None)
        assert url is None

    def test_get_image_url_original(self, tmdb_service):
        """Test image URL with original size."""
        url = tmdb_service.get_image_url("/poster.jpg", "original")
        assert url == "https://image.tmdb.org/t/p/original/poster.jpg"

    def test_parse_date(self, tmdb_service):
        """Test date parsing."""
        from datetime import date as dt_date

        result = tmdb_service._parse_date("2024-01-15")
        assert result == dt_date(2024, 1, 15)

    def test_parse_date_invalid(self, tmdb_service):
        """Test invalid date parsing."""
        result = tmdb_service._parse_date("invalid")
        assert result is None

    def test_parse_date_none(self, tmdb_service):
        """Test None date parsing."""
        result = tmdb_service._parse_date(None)
        assert result is None

    def test_is_bearer_token(self, tmdb_service):
        """Test bearer token detection."""
        assert tmdb_service._is_bearer_token("eyJhbGciOiJIUzI1NiJ9.xxx") is True
        assert tmdb_service._is_bearer_token("abc123apikey") is False


class TestTMDBServiceAPIToken:
    """Tests for TMDBService API token methods."""

    @pytest.fixture
    def temp_db(self):
        """Create a temporary database for testing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            yield db_path

    @pytest.fixture
    def tmdb_service(self, temp_db):
        """Provide a TMDBService instance."""
        config_service = ConfigService(db_path=temp_db)
        return TMDBService(config_service=config_service)

    @pytest.mark.asyncio
    async def test_get_api_token_status_not_configured(self, tmdb_service):
        """Test status when no API token is configured."""
        status = await tmdb_service.get_api_token_status()
        assert status.is_configured is False

    @pytest.mark.asyncio
    async def test_save_and_verify_empty_token(self, tmdb_service):
        """Test save with empty token."""
        status = await tmdb_service.save_and_verify_api_token("")
        assert status.is_configured is False
        assert status.is_valid is False
        assert status.error_message is not None

    @pytest.mark.asyncio
    async def test_save_and_verify_mocked_success(self, tmdb_service):
        """Test save with mocked successful verification."""
        with patch.object(
            tmdb_service, "verify_api_token", new_callable=AsyncMock
        ) as mock_verify:
            mock_verify.return_value = (True, None)

            status = await tmdb_service.save_and_verify_api_token("valid_token")

            assert status.is_configured is True
            assert status.is_valid is True
            assert status.error_message is None

    @pytest.mark.asyncio
    async def test_save_and_verify_mocked_failure(self, tmdb_service):
        """Test save with mocked failed verification."""
        with patch.object(
            tmdb_service, "verify_api_token", new_callable=AsyncMock
        ) as mock_verify:
            mock_verify.return_value = (False, "Invalid API key")

            status = await tmdb_service.save_and_verify_api_token("invalid_token")

            assert status.is_configured is False
            assert status.is_valid is False
            assert "Invalid" in status.error_message

    @pytest.mark.asyncio
    async def test_verify_api_token_timeout(self, tmdb_service):
        """Test API token verification with timeout."""
        import httpx

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.get.side_effect = httpx.TimeoutException("timeout")
            mock_client.return_value.__aenter__.return_value = mock_instance

            is_valid, error = await tmdb_service.verify_api_token("test_token")

            assert is_valid is False
            assert "超时" in error

    @pytest.mark.asyncio
    async def test_test_proxy_socks5_missing_support(self, tmdb_service):
        """Test SOCKS5 proxy reports missing runtime support clearly."""
        success, message, latency = await tmdb_service.test_proxy("socks5://127.0.0.1:1080")

        assert success is False
        assert latency is None
        assert message == "测试失败: SOCKS5 代理缺少运行依赖，请安装 httpx[socks]"


class TestTMDBServiceSearch:
    """Tests for TMDBService search and metadata methods."""

    @pytest.fixture
    def temp_db(self):
        """Create a temporary database for testing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            yield db_path

    @pytest.fixture
    def config_service(self, temp_db):
        """Provide a ConfigService instance."""
        return ConfigService(db_path=temp_db)

    @pytest.fixture
    def tmdb_service(self, config_service):
        """Provide a TMDBService instance."""
        return TMDBService(config_service=config_service)

    @pytest.mark.asyncio
    async def test_search_series_by_api_mocked(self, tmdb_service):
        """Test search with mocked API response."""
        mock_json = {
            "results": [
                {
                    "id": 1396,
                    "name": "Breaking Bad",
                    "original_name": "Breaking Bad",
                    "first_air_date": "2008-01-20",
                    "poster_path": "/poster.jpg",
                    "overview": "A chemistry teacher...",
                    "vote_average": 8.9,
                    "adult": False,
                }
            ],
            "total_results": 1,
        }

        with patch.object(
            tmdb_service, "_make_api_request", new_callable=AsyncMock
        ) as mock_request:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = mock_json
            mock_request.return_value = mock_response

            result = await tmdb_service.search_series_by_api("Breaking Bad")

            assert result.query == "Breaking Bad"
            assert result.total_results == 1
            assert len(result.results) == 1
            assert result.results[0].id == 1396
            assert result.results[0].name == "Breaking Bad"

    @pytest.mark.asyncio
    async def test_search_series_by_api_timeout(self, tmdb_service):
        """Test search with timeout."""
        import httpx

        with patch.object(
            tmdb_service, "_make_api_request", new_callable=AsyncMock
        ) as mock_request:
            mock_request.side_effect = httpx.TimeoutException("timeout")

            with pytest.raises(httpx.TimeoutException):
                await tmdb_service.search_series_by_api("test")

    @pytest.mark.asyncio
    async def test_get_series_by_api_mocked(self, tmdb_service):
        """Test getting series with mocked API response."""
        mock_json = {
            "id": 1396,
            "name": "Breaking Bad",
            "original_name": "Breaking Bad",
            "overview": "A chemistry teacher...",
            "first_air_date": "2008-01-20",
            "vote_average": 8.9,
            "poster_path": "/poster.jpg",
            "backdrop_path": "/backdrop.jpg",
            "genres": [{"id": 18, "name": "Drama"}],
            "status": "Ended",
            "number_of_seasons": 5,
            "number_of_episodes": 62,
            "seasons": [
                {
                    "season_number": 1,
                    "name": "Season 1",
                    "episode_count": 7,
                }
            ],
        }

        with patch.object(
            tmdb_service, "_make_api_request", new_callable=AsyncMock
        ) as mock_request:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = mock_json
            mock_request.return_value = mock_response

            result = await tmdb_service.get_series_by_api(1396)

            assert result is not None
            assert result.id == 1396
            assert result.name == "Breaking Bad"
            assert len(result.genres) == 1
            assert result.genres[0] == "Drama"

    @pytest.mark.asyncio
    async def test_get_series_by_api_not_found(self, tmdb_service):
        """Test getting non-existent series."""
        with patch.object(
            tmdb_service, "_make_api_request", new_callable=AsyncMock
        ) as mock_request:
            mock_response = MagicMock()
            mock_response.status_code = 404
            mock_request.return_value = mock_response

            result = await tmdb_service.get_series_by_api(99999999)

            assert result is None

    @pytest.mark.asyncio
    async def test_get_season_by_api_mocked(self, tmdb_service):
        """Test getting season with mocked API response."""
        mock_json = {
            "season_number": 1,
            "name": "Season 1",
            "overview": "The first season...",
            "air_date": "2008-01-20",
            "poster_path": "/season1.jpg",
            "episodes": [
                {
                    "episode_number": 1,
                    "name": "Pilot",
                    "overview": "The first episode.",
                    "air_date": "2008-01-20",
                    "vote_average": 8.5,
                    "still_path": "/ep1.jpg",
                },
                {
                    "episode_number": 2,
                    "name": "Cat's in the Bag",
                    "overview": "The second episode.",
                    "air_date": "2008-01-27",
                    "vote_average": 8.3,
                    "still_path": "/ep2.jpg",
                },
            ],
        }

        with patch.object(
            tmdb_service, "_make_api_request", new_callable=AsyncMock
        ) as mock_request:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = mock_json
            mock_request.return_value = mock_response

            result = await tmdb_service.get_season_by_api(1396, 1)

            assert result is not None
            assert result.season_number == 1
            assert result.name == "Season 1"
            assert len(result.episodes) == 2
            assert result.episodes[0].name == "Pilot"

    @pytest.mark.asyncio
    async def test_get_season_by_api_not_found(self, tmdb_service):
        """Test getting non-existent season."""
        with patch.object(
            tmdb_service, "_make_api_request", new_callable=AsyncMock
        ) as mock_request:
            mock_response = MagicMock()
            mock_response.status_code = 404
            mock_request.return_value = mock_response

            result = await tmdb_service.get_season_by_api(1396, 99)

            assert result is None

    def test_parse_series_json(self, tmdb_service):
        """Test parsing series JSON."""
        data = {
            "id": 1396,
            "name": "Breaking Bad",
            "original_name": "Breaking Bad",
            "overview": "A chemistry teacher...",
            "first_air_date": "2008-01-20",
            "vote_average": 8.9,
            "poster_path": "/poster.jpg",
            "backdrop_path": "/backdrop.jpg",
            "genres": [{"id": 18, "name": "Drama"}],
            "status": "Ended",
            "number_of_seasons": 5,
            "number_of_episodes": 62,
            "seasons": [],
        }
        result = tmdb_service._parse_series_json(data)

        assert result.id == 1396
        assert result.name == "Breaking Bad"
        assert result.vote_average == 8.9
        assert result.genres == ["Drama"]

    def test_parse_season_json(self, tmdb_service):
        """Test parsing season JSON."""
        data = {
            "season_number": 1,
            "name": "Season 1",
            "overview": "The first season...",
            "air_date": "2008-01-20",
            "poster_path": "/season1.jpg",
            "episodes": [
                {
                    "episode_number": 1,
                    "name": "Pilot",
                    "overview": "The first episode.",
                    "air_date": "2008-01-20",
                    "vote_average": 8.5,
                    "still_path": "/ep1.jpg",
                }
            ],
        }
        result = tmdb_service._parse_season_json(data)

        assert result.season_number == 1
        assert result.name == "Season 1"
        assert len(result.episodes) == 1
        assert result.episodes[0].name == "Pilot"
