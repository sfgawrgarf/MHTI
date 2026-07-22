"""Safety tests for AI-assisted automatic scraping."""

from server.models.ai import AIRecognitionResult
from server.services.scraper_service import _can_auto_apply_ai_result


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
