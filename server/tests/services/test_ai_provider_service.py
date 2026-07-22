import pytest

from server.models.ai import AIRecognitionResult
from server.services.ai_provider_service import AIProviderError, AIProviderService


def test_ai_response_json_accepts_markdown_fence() -> None:
    payload = chr(96) * 3 + "json\n" + '{"title":"示例","confidence":0.9}' + "\n" + chr(96) * 3
    assert AIProviderService._json_from_response(payload)["title"] == "示例"


def test_ai_response_json_rejects_non_json() -> None:
    with pytest.raises(AIProviderError):
        AIProviderService._json_from_response("not-json")


def test_ai_recognition_result_preserves_search_titles() -> None:
    result = AIRecognitionResult.model_validate(
        {"title": "作品名", "search_titles": ["作品名", "Romaji title"]}
    )

    assert result.search_titles == ["作品名", "Romaji title"]
