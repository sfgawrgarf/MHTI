"""Regression coverage for candidate verification, specials and upstream outages."""

from unittest.mock import AsyncMock, Mock

import pytest

from server.core.exceptions import TMDBError, TMDBRateLimitError, TMDBTimeoutError
from server.models.ai import AIConfig, AIRecognitionResult
from server.models.scraper import ScrapeByIdRequest, ScrapeRequest, ScrapeStatus
from server.models.tmdb import TMDBSearchResponse, TMDBSearchResult, TMDBSeason, TMDBSeries
from server.services.ai_provider_service import AIProviderService
from server.services.parser_service import ParserService
from server.services.scraper_service import ScraperService


@pytest.fixture
def scraper(monkeypatch):
    candidate = TMDBSearchResult(id=123, name="Known Title", adult=True)
    tmdb = Mock(
        search_series_by_api=AsyncMock(return_value=TMDBSearchResponse(
            query="Known Title", total_results=1, results=[candidate],
        )),
        get_series_by_api=AsyncMock(return_value=None),
        get_season_by_api=AsyncMock(return_value=None),
    )
    service = ScraperService(
        config_service=Mock(), tmdb_service=tmdb, parser_service=ParserService(),
        nfo_service=Mock(), rename_service=Mock(), image_service=Mock(),
        subtitle_service=Mock(), emby_service=Mock(),
    )
    service._lookup_confirmed_alias = AsyncMock(return_value=None)
    service._enrich_search_results = AsyncMock(return_value=[candidate])
    monkeypatch.setattr(AIProviderService, "get_config", AsyncMock(return_value=AIConfig()))
    return service


@pytest.mark.asyncio
@pytest.mark.parametrize("title, selected", [("Completely Different Series", False), ("Known Title", True)])
async def test_single_candidate_must_pass_title_verification(scraper, temp_dir, title, selected):
    source = temp_dir / "Known Title S01E01.strm"
    source.write_text("https://example.invalid/video")
    candidate = TMDBSearchResult(id=123, name=title, adult=True)
    scraper.tmdb_service.search_series_by_api.return_value = TMDBSearchResponse(
        query="Known Title", total_results=1, results=[candidate],
    )
    result = await scraper.scrape_file(ScrapeRequest(file_path=str(source)))
    if selected:
        assert result.selected_id == 123
        scraper.tmdb_service.get_series_by_api.assert_awaited_once_with(123)
    else:
        assert result.status == ScrapeStatus.NEED_SELECTION
        assert result.selected_id is None
        scraper.tmdb_service.get_series_by_api.assert_not_awaited()
    assert not scraper.rename_service.mock_calls
    assert source.exists()


@pytest.mark.parametrize("requested, seasons, expected", [
    (0, [0, 1], 0), (0, [1], 0), (0, [], 0),
    (2, [0, 1, 2], 2), (9, [1], 1), (9, [1, 2, 3], 9),
])
def test_season_correction_preserves_specials_and_existing_seasons(scraper, requested, seasons, expected):
    series = TMDBSeries(id=123, name="Known Title", seasons=[
        TMDBSeason(season_number=number, name=f"Season {number}") for number in seasons
    ])
    corrected, message = scraper._auto_correct_season(requested, series)
    assert corrected == expected
    if requested == expected:
        assert message is None


@pytest.mark.asyncio
@pytest.mark.parametrize("explicit", [False, True])
@pytest.mark.parametrize("has_specials", [False, True])
async def test_specials_reach_tmdb_as_season_zero(scraper, temp_dir, explicit, has_specials):
    source = temp_dir / "Known Title S00E01.strm"
    source.write_text("https://example.invalid/video")
    seasons = [TMDBSeason(season_number=1, name="Season 1")]
    if has_specials:
        seasons.append(TMDBSeason(season_number=0, name="Specials"))
    scraper.tmdb_service.get_series_by_api.return_value = TMDBSeries(
        id=123, name="Known Title", seasons=seasons,
    )
    if explicit:
        result = await scraper.scrape_by_id(ScrapeByIdRequest(
            file_path=str(source), tmdb_id=123, season=0, episode=1,
        ))
    else:
        result = await scraper.scrape_file(ScrapeRequest(file_path=str(source)))
    scraper.tmdb_service.get_season_by_api.assert_awaited_once_with(123, 0)
    assert result.parsed_season == 0
    assert result.status == ScrapeStatus.NEED_SEASON_EPISODE
    assert not scraper.rename_service.mock_calls
    assert source.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [TMDBRateLimitError(), TMDBTimeoutError(), TMDBError("服务异常")])
async def test_search_failure_is_not_no_match(scraper, temp_dir, failure):
    source = temp_dir / "Known Title S01E01.strm"
    source.touch()
    scraper.tmdb_service.search_series_by_api.side_effect = failure
    result = await scraper.scrape_file(ScrapeRequest(file_path=str(source)))
    assert result.status == ScrapeStatus.SEARCH_FAILED
    assert result.message == str(failure)
    scraper.tmdb_service.get_series_by_api.assert_not_awaited()
    assert not scraper.rename_service.mock_calls


@pytest.mark.asyncio
@pytest.mark.parametrize("explicit", [False, True])
@pytest.mark.parametrize("stage", ["series", "season"])
async def test_detail_failure_does_not_request_manual_season_selection(scraper, temp_dir, explicit, stage):
    source = temp_dir / "Known Title S01E01.strm"
    source.touch()
    scraper.tmdb_service.get_series_by_api.return_value = TMDBSeries(
        id=123, name="Known Title", seasons=[TMDBSeason(season_number=1, name="Season 1")],
    )
    getattr(scraper.tmdb_service, f"get_{stage}_by_api").side_effect = TMDBRateLimitError()
    if explicit:
        result = await scraper.scrape_by_id(ScrapeByIdRequest(
            file_path=str(source), tmdb_id=123, season=1, episode=1,
        ))
    else:
        result = await scraper.scrape_file(ScrapeRequest(file_path=str(source)))
    assert result.status == ScrapeStatus.API_FAILED
    assert "过于频繁" in result.message
    assert not scraper.rename_service.mock_calls
    assert source.exists()


@pytest.mark.asyncio
async def test_multilingual_episode_failure_is_not_missing_episode(scraper, temp_dir):
    source = temp_dir / "Known Title.strm"
    source.touch()
    scraper.tmdb_service.get_series_by_api.return_value = TMDBSeries(
        id=123, name="Known Title", number_of_episodes=2,
        seasons=[TMDBSeason(season_number=1, name="Season 1")],
    )
    scraper.tmdb_service.get_season_by_api.side_effect = TMDBTimeoutError()
    result = await scraper.scrape_file(ScrapeRequest(file_path=str(source)))
    assert result.status == ScrapeStatus.API_FAILED
    assert "超时" in result.message
    assert not scraper.rename_service.mock_calls


@pytest.mark.asyncio
async def test_ai_expanded_search_failure_is_not_empty_result(scraper, temp_dir, monkeypatch):
    source = temp_dir / "Known Title.strm"
    source.touch()

    async def search(query):
        if query == "Suggested Alternative":
            raise TMDBRateLimitError()
        return TMDBSearchResponse(query=query, total_results=0, results=[])

    scraper.tmdb_service.search_series_by_api.side_effect = search
    monkeypatch.setattr(AIProviderService, "get_config", AsyncMock(return_value=AIConfig(
        enabled=True, model="test", api_key="test",
    )))
    monkeypatch.setattr(AIProviderService, "recognize", AsyncMock(return_value=AIRecognitionResult(
        title="Suggested Alternative", search_titles=["Suggested Alternative"],
        confidence=0.5, needs_confirmation=True,
    )))
    result = await scraper.scrape_file(ScrapeRequest(file_path=str(source)))
    assert result.status == ScrapeStatus.SEARCH_FAILED
    assert "过于频繁" in result.message
    assert not scraper.rename_service.mock_calls
