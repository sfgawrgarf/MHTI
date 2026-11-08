"""Preview regressions cover both services and the actual HTTP and batch routes."""

from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from server.api.deps import get_scraper_service
from server.application.scraping.preview import ScrapePreviewService
from server.application.scraping.service import ScraperService
from server.common.exceptions import (
    TMDBError,
    TMDBInvalidCredentialsError,
    TMDBNotConfiguredError,
    TMDBRateLimitError,
    TMDBTimeoutError,
)
from server.common.path_security import PathSecurityError
from server.domain.metadata.tmdb_service import TMDBService
from server.domain.parsing.parser_service import ParserService
from server.main import app
from server.models.parser import ParsedInfo


@pytest.fixture
def preview_services(config_service, monkeypatch):
    tmdb = TMDBService(config_service)
    request = AsyncMock(return_value=httpx.Response(200, json={
        "results": [
            {"id": 1, "name": "Example Show", "first_air_date": "2020-01-01"},
            {"id": 2, "name": "Example Show", "first_air_date": "2021-01-01"},
            {"id": 3, "name": "Example Show"},
        ],
    }))
    monkeypatch.setattr(tmdb, "_make_api_request", request)
    parser = ParserService()
    scraper = ScraperService(
        config_service=config_service, tmdb_service=tmdb, parser_service=parser,
        nfo_service=None, rename_service=None, image_service=None,
        subtitle_service=None, emby_service=None,
    )
    standalone = ScrapePreviewService(parser, tmdb)
    return scraper, standalone, request


@pytest.mark.asyncio
@pytest.mark.parametrize("service_index", [0, 1], ids=["actual-service", "standalone"])
@pytest.mark.parametrize(("year", "expected_ids"), [(None, [1, 2, 3]), (2020, [1]), (2022, [])])
async def test_preview_uses_sync_parser_and_year_filter(
    tmp_path, preview_services, service_index, year, expected_ids,
):
    name = f"Example.Show.{year}.S01E01.mkv" if year else "Example.Show.S01E01.mkv"
    media = tmp_path / name
    media.write_bytes(b"unchanged media")
    preview = await preview_services[service_index].preview(str(media))
    assert preview.parsed_title == "Example Show"
    assert preview.parsed_season == 1
    assert preview.parsed_episode == 1
    assert [item.id for item in preview.search_results] == expected_ids
    assert media.read_bytes() == b"unchanged media"
    assert list(tmp_path.iterdir()) == [media]


@pytest.mark.asyncio
@pytest.mark.parametrize("service_index", [0, 1])
async def test_no_title_skips_search(tmp_path, preview_services, service_index, monkeypatch):
    media = tmp_path / "S01E01.mkv"
    media.touch()
    service = preview_services[service_index]
    parser = Mock(spec=ParserService)
    parser.parse.return_value = ParsedInfo(original_filename=media.name, season=1, episode=1)
    monkeypatch.setattr(service, "parser_service", parser)
    preview = await service.preview(str(media))
    assert preview.parsed_title is None
    assert not preview.search_results
    preview_services[2].assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("service_index", [0, 1])
async def test_preview_rejects_paths_outside_media_roots(
    tmp_path, preview_services, service_index, monkeypatch,
):
    media = tmp_path / "Example.Show.S01E01.mkv"
    media.touch()
    monkeypatch.setenv("MHTI_ALLOWED_MEDIA_ROOTS", str(tmp_path / "allowed"))
    with pytest.raises(PathSecurityError):
        await preview_services[service_index].preview(str(media))
    preview_services[2].assert_not_awaited()


@pytest.mark.parametrize("batch", [False, True], ids=["single-http", "batch-dry-run"])
@pytest.mark.parametrize(("year", "expected_ids"), [(None, [1, 2, 3]), (2020, [1]), (2022, [])])
def test_http_preview_and_batch_return_filtered_candidates(
    auth_client, tmp_path, preview_services, batch, year, expected_ids,
):
    scraper, _, request = preview_services
    app.dependency_overrides[get_scraper_service] = lambda: scraper
    name = f"Example.Show.{year}.S01E01.mkv" if year else "Example.Show.S01E01.mkv"
    media = tmp_path / name
    media.touch()
    if batch:
        response = auth_client.post(
            "/api/scraper/batch", json={"file_paths": [str(media)], "dry_run": True},
        )
    else:
        response = auth_client.post("/api/scraper/preview", json={"file_path": str(media)})
    assert response.status_code == 200, response.text
    data = response.json()["results"][0] if batch else response.json()
    assert data["parsed_title"] == "Example Show"
    assert data["parsed_season"] == 1
    assert data["parsed_episode"] == 1
    assert [item["id"] for item in data["search_results"]] == expected_ids
    params = request.call_args.kwargs["params"]
    if year is None:
        assert "first_air_date_year" not in params
    else:
        assert params["first_air_date_year"] == year


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (TMDBNotConfiguredError("API Token"), 502),
        (TMDBInvalidCredentialsError("API Token"), 502),
        (TMDBRateLimitError(10), 502),
        (TMDBTimeoutError("/search/tv"), 408),
        (TMDBError("TMDB 服务请求失败 (HTTP 403)"), 502),
    ],
)
def test_http_preview_preserves_tmdb_errors(
    auth_client, tmp_path, preview_services, error, status,
):
    scraper, _, request = preview_services
    request.side_effect = error
    app.dependency_overrides[get_scraper_service] = lambda: scraper
    media = tmp_path / "Example.Show.2020.S01E01.mkv"
    media.touch()
    response = auth_client.post("/api/scraper/preview", json={"file_path": str(media)})
    assert response.status_code == status
    assert "error" in response.json()
