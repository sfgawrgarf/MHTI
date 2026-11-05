"""Search fallback preserves original titles and the AI/alias decision paths."""

from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from server.domain.parsing.parser_service import ParserService
from server.models.organize import OrganizeMode
from server.models.scraper import ScrapeRequest, ScrapeStatus
from server.models.tmdb import TMDBSearchResponse, TMDBSearchResult
from server.infrastructure.db import close_database
from server.tests.application.test_scraper_service import FakeTMDB, _build_service, _patch_tail


@pytest_asyncio.fixture(autouse=True)
async def close_test_pool():
    yield
    await close_database()


def response(results):
    return TMDBSearchResponse(query='test', total_results=len(results), results=results)


@pytest.mark.asyncio
@pytest.mark.parametrize('original_matches', [True, False])
async def test_studio_prefix_is_only_stripped_after_search_misses(temp_db, tmp_path, monkeypatch, original_matches):
    source = tmp_path / 'Queen_Bee作品名 第1話.mkv'
    source.write_bytes(b'video')
    candidate = TMDBSearchResult(id=123, name='Test Show', adult=True)
    tmdb = FakeTMDB()
    tmdb.search_series_by_api.side_effect = [response([candidate])] if original_matches else [response([]), response([candidate])]
    service = _build_service(temp_db, tmdb, parser=ParserService())
    _patch_tail(monkeypatch, service)
    result = await service.scrape_file(ScrapeRequest(
        file_path=str(source), output_dir=str(tmp_path / 'out'), link_mode=OrganizeMode.COPY,
    ))
    assert result.status == ScrapeStatus.SUCCESS
    calls = [call.args[0] for call in tmdb.search_series_by_api.await_args_list]
    assert calls == (['Queen Bee作品名'] if original_matches else ['Queen Bee作品名', '作品名'])
    assert source.exists()


@pytest.mark.asyncio
async def test_known_negative_permission_explains_failure_before_search(temp_db, tmp_path, monkeypatch):
    source = tmp_path / '作品名 第1話.mkv'
    source.write_bytes(b'video')
    tmdb = FakeTMDB()
    service = _build_service(temp_db, tmdb, parser=ParserService())
    monkeypatch.setattr(service.config_service, 'get_adult_status', AsyncMock(return_value=(False, 'Token 未开启 R18', None)))
    result = await service.scrape_file(ScrapeRequest(file_path=str(source)))
    assert result.status == ScrapeStatus.API_FAILED and 'R18' in result.message
    tmdb.search_series_by_api.assert_not_awaited()


@pytest.mark.asyncio
async def test_permission_probe_read_failure_does_not_block_search(temp_db, tmp_path, monkeypatch):
    source = tmp_path / '作品名 第1話.mkv'
    source.write_bytes(b'video')
    tmdb = FakeTMDB(search_results=[TMDBSearchResult(id=123, name='Test Show', adult=True)])
    service = _build_service(temp_db, tmdb, parser=ParserService())
    _patch_tail(monkeypatch, service)
    monkeypatch.setattr(service.config_service, 'get_adult_status', AsyncMock(side_effect=RuntimeError('unavailable')))
    result = await service.scrape_file(ScrapeRequest(file_path=str(source), output_dir=str(tmp_path / 'out'), link_mode=OrganizeMode.COPY))
    assert result.status == ScrapeStatus.SUCCESS
    tmdb.search_series_by_api.assert_awaited_once()
