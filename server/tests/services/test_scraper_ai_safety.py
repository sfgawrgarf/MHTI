"""Safety tests for AI-assisted automatic scraping."""

from unittest.mock import AsyncMock, Mock

import pytest

from server.models.ai import AIConfig, AIRecognitionResult, AIUsageMode
from server.models.scraper import ScrapeRequest, ScrapeStatus
from server.models.tmdb import TMDBSearchResponse, TMDBSearchResult
from server.services.ai_provider_service import AIProviderService
from server.services.parser_service import ParserService
from server.services.scraper_service import (
    ScraperService,
    _can_auto_apply_ai_result,
    _should_use_ai,
)


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


def test_assist_mode_only_runs_when_regular_search_has_no_adult_candidate() -> None:
    assert _should_use_ai(
        AIUsageMode.ASSIST_USE,
        has_confirmed_alias=False,
        has_adult_candidates=False,
    ) is True
    assert _should_use_ai(
        AIUsageMode.ASSIST_USE,
        has_confirmed_alias=False,
        has_adult_candidates=True,
    ) is False


def test_force_mode_runs_without_alias_even_when_regular_search_has_candidates() -> None:
    assert _should_use_ai(
        AIUsageMode.FORCE_USE,
        has_confirmed_alias=False,
        has_adult_candidates=True,
    ) is True
    assert _should_use_ai(
        AIUsageMode.FORCE_USE,
        has_confirmed_alias=True,
        has_adult_candidates=False,
    ) is False


@pytest.mark.asyncio
async def test_force_mode_blocks_deterministic_auto_selection_after_low_confidence_ai(
    temp_dir,
    monkeypatch,
) -> None:
    source = temp_dir / "Known Title.strm"
    source.write_text("https://example.invalid/video")
    candidate = TMDBSearchResult(
        id=456,
        name="Known Title",
        original_name="Known Title",
        adult=True,
    )

    tmdb_service = Mock()
    tmdb_service.search_series_by_api = AsyncMock(return_value=TMDBSearchResponse(
        query="Known Title",
        total_results=1,
        results=[candidate],
    ))
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
    recognize = AsyncMock(return_value=AIRecognitionResult(
        confidence=0.5,
        needs_confirmation=True,
        reason="候选无法确认",
    ))
    monkeypatch.setattr(
        AIProviderService,
        "get_config",
        AsyncMock(return_value=AIConfig(
            enabled=True,
            usage_mode=AIUsageMode.FORCE_USE,
            model="test",
            api_key="key",
        )),
    )
    monkeypatch.setattr(AIProviderService, "recognize", recognize)

    result = await service.scrape_file(ScrapeRequest(file_path=str(source)))

    assert result.status == ScrapeStatus.NEED_SELECTION
    assert result.selected_id is None
    assert "强制识别未给出高置信度" in (result.message or "")
    recognize.assert_awaited_once()


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
