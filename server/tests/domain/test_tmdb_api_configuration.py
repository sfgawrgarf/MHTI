"""Deployment API roots and premiere-year searches use the real request path."""

import os
import subprocess
import sys
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import httpx
import pytest

from server.domain.metadata import tmdb_service as tmdb_module
from server.domain.metadata.tmdb_service import TMDBService


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        (None, "https://api.themoviedb.org/3"),
        ("", "https://api.themoviedb.org/3"),
        ("  ", "https://api.themoviedb.org/3"),
        (" https://proxy.example/3/// ", "https://proxy.example/3"),
        ("https://proxy.example/prefix/3", "https://proxy.example/prefix/3"),
        ("http://tmdb-proxy:8080", "http://tmdb-proxy:8080"),
    ],
)
def test_api_root_configuration(monkeypatch, configured, expected):
    if configured is None:
        monkeypatch.delenv("MHTI_TMDB_API_BASE_URL", raising=False)
    else:
        monkeypatch.setenv("MHTI_TMDB_API_BASE_URL", configured)
    assert tmdb_module._get_tmdb_api_base_url() == expected


@pytest.mark.parametrize(
    "configured",
    [
        "/3", "ftp://proxy.example/3", "https:///3", "https://proxy.example:bad/3",
        "https://proxy.example:65536/3", "https://proxy.example:0/3",
        "https://user:secret@proxy.example/3", "https://proxy.example/3?api_key=secret",
        "https://proxy.example/3#secret", "https://proxy.example/a b",
        "https://proxy.example\\secret/3", "https://[invalid/3",
    ],
)
def test_invalid_api_root_fails_without_echoing_credentials(monkeypatch, configured):
    monkeypatch.setenv("MHTI_TMDB_API_BASE_URL", configured)
    with pytest.raises(ValueError, match="MHTI_TMDB_API_BASE_URL") as error:
        tmdb_module._get_tmdb_api_base_url()
    assert "secret" not in str(error.value)


def test_api_root_is_loaded_when_process_starts():
    env = dict(os.environ, MHTI_TMDB_API_BASE_URL="https://proxy.example/prefix/3/")
    result = subprocess.run(
        [sys.executable, "-c",
         "from server.domain.metadata.tmdb_service import TMDB_API_BASE_URL; "
         "print(TMDB_API_BASE_URL)"],
        env=env, capture_output=True, text=True, check=True,
    )
    assert result.stdout.strip() == "https://proxy.example/prefix/3"


@pytest.mark.asyncio
@pytest.mark.parametrize("token", ["example-api-key", "eyJ.example-bearer"])
async def test_all_api_operations_use_configured_root_and_auth(
    monkeypatch, config_service, token,
):
    monkeypatch.setenv("MHTI_TMDB_API_BASE_URL", "https://proxy.example/prefix/3/")
    monkeypatch.setattr(tmdb_module, "TMDB_API_BASE_URL", tmdb_module._get_tmdb_api_base_url())
    service = TMDBService(config_service)
    await config_service.save_api_token(token)
    requests = []

    def respond(request):
        requests.append(request)
        payload = {"id": 42, "name": "Example Show", "seasons": [],
                   "season_number": 1, "episodes": [], "results": []}
        return httpx.Response(200, json=payload)

    @asynccontextmanager
    async def client_context(**kwargs):
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            yield client

    monkeypatch.setattr(tmdb_module, "shared_http_client", client_context)
    await service.search_series_by_api("Example Show")
    assert await service.get_series_by_api(42) is not None
    assert await service.get_season_by_api(42, 1) is not None
    assert await service.verify_api_token(token) == (True, None)
    assert [request.url.path for request in requests] == [
        "/prefix/3/search/tv", "/prefix/3/tv/42", "/prefix/3/tv/42/season/1",
        "/prefix/3/configuration",
    ]
    for request in requests:
        assert request.url.host == "proxy.example"
        if token.startswith("eyJ"):
            assert request.headers["Authorization"] == f"Bearer {token}"
            assert "api_key" not in request.url.params
        else:
            assert request.url.params["api_key"] == token
            assert "Authorization" not in request.headers
    assert service.get_image_url("/poster.jpg") == "https://image.tmdb.org/t/p/w500/poster.jpg"


@pytest.mark.asyncio
@pytest.mark.parametrize(("year", "expected_ids"), [(None, [1, 2, 3, 4]), (2020, [1]), (2022, [])])
async def test_year_is_sent_upstream_and_candidates_are_checked(
    monkeypatch, config_service, year, expected_ids,
):
    service = TMDBService(config_service)
    request = AsyncMock(return_value=httpx.Response(200, json={
        "total_results": 44,
        "results": [
            {"id": 1, "name": "Example Show", "first_air_date": "2020-01-01"},
            {"id": 2, "name": "Example Show", "first_air_date": "2021-01-01"},
            {"id": 3, "name": "Example Show", "first_air_date": ""},
            {"id": 4, "name": "Example Show", "first_air_date": "invalid"},
        ],
    }))
    monkeypatch.setattr(service, "_make_api_request", request)
    response = await service.search_series_by_api(
        " Example Show ", "en-US", first_air_date_year=year,
    )
    assert [item.id for item in response.results] == expected_ids
    assert response.total_results == 44  # Upstream total, not the filtered page length.
    params = request.call_args.kwargs["params"]
    assert params["query"] == "Example Show"
    assert params["language"] == "en-US"
    assert params["include_adult"] == "true"
    assert "year" not in params  # This would also match later episode air dates.
    if year is None:
        assert "first_air_date_year" not in params
    else:
        assert params["first_air_date_year"] == year


@pytest.mark.asyncio
@pytest.mark.parametrize("year", [999, 10000])
async def test_invalid_search_year_does_not_send_request(monkeypatch, config_service, year):
    service = TMDBService(config_service)
    request = AsyncMock()
    monkeypatch.setattr(service, "_make_api_request", request)
    with pytest.raises(ValueError, match="first_air_date_year"):
        await service.search_series_by_api("Example Show", first_air_date_year=year)
    request.assert_not_awaited()
