"""Safety tests for AI-assisted automatic scraping."""

from unittest.mock import AsyncMock, Mock

import pytest

from server.models.ai import AIConfig, AIRecognitionResult
from server.models.scraper import ScrapeRequest, ScrapeStatus
from server.models.tmdb import TMDBSearchResponse, TMDBSearchResult
from server.services.ai_provider_service import AIProviderService
from server.services.parser_service import ParserService
from server.services.scraper_service import ScraperService, _can_auto_apply_ai_result


def test_low_confidence_ai_result_cannot_change_automatic_scrape() -> None:
    result = AIRecognitionResult(
        title="错误标题",
        season=9,
        episode=99,
        confidence=0.2,
        needs_confirmation=True,
    )

    assert _can_auto_apply_ai_result(result) is False


def test_confirmed_ai_result_can_change_automatic_scrape() -> None:
    result = AIRecognitionResult(
        title="正确标题",
        season=1,
        episode=2,
        confidence=0.95,
        needs_confirmation=False,
    )

    assert _can_auto_apply_ai_result(result) is True


@pytest.mark.asyncio
async def test_low_confidence_ai_alias_finds_candidates_but_cannot_auto_select(
    temp_dir,
    monkeypatch,
) -> None:
    source = temp_dir / "Unhelpful Original Title.strm"
    source.write_text("https://example.invalid/video")
    candidate = TMDBSearchResult(
        id=123,
        name="AI Canonical Title",
        original_name="AI Canonical Title",
        adult=True,
    )

    async def search(query: str):
        results = [candidate] if query == "AI Canonical Title" else []
        return TMDBSearchResponse(
            query=query,
            total_results=len(results),
            results=results,
        )

    tmdb_service = Mock()
    tmdb_service.search_series_by_api = AsyncMock(side_effect=search)
    service = ScraperService(
        config_service=Mock(),
        tmdb_service=tmdb_service,
        parser_service=ParserService(),
        nfo_service=Mock(),
        rename_service=Mock(),
        image_service=Mock(),
        subtitle_service=Mock(),
        emby_service=Mock(),
    )
    service._lookup_confirmed_alias = AsyncMock(return_value=None)
    service._enrich_search_results = AsyncMock(return_value=[candidate])

    monkeypatch.setattr(
        AIProviderService,
        "get_config",
        AsyncMock(return_value=AIConfig(enabled=True, model="test", api_key="key")),
    )
    monkeypatch.setattr(
        AIProviderService,
        "recognize",
        AsyncMock(return_value=AIRecognitionResult(
            title="AI Canonical Title",
            search_titles=["AI Canonical Title"],
            confidence=0.5,
            needs_confirmation=True,
            reason="没有候选可确认",
        )),
    )

    result = await service.scrape_file(ScrapeRequest(file_path=str(source)))

    assert result.status == ScrapeStatus.NEED_SELECTION
    assert result.selected_id is None
    assert [item.id for item in result.search_results or []] == [123]
    assert "置信度不足" in (result.message or "")
