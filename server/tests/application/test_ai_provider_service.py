"""Tests for the OpenAI-compatible recognition provider boundary."""

from typing import Any

import httpx
import pytest

from server.application.ai_provider_service import (
    AIProviderError,
    AIProviderService,
)
from server.models.ai import AICandidate, AIConfig


class FakeConfigStore:
    def __init__(self, raw: str | None = None) -> None:
        self.raw = raw
        self.saved: tuple[str, bool] | None = None
        self.deleted = False

    async def get(self, key: str, *, encrypted: bool = False) -> str | None:
        assert key == "ai_recognition_config"
        assert encrypted is True
        return self.raw

    async def set(self, key: str, value: str, *, encrypted: bool = False) -> None:
        assert key == "ai_recognition_config"
        self.raw = value
        self.saved = (value, encrypted)

    async def delete(self, key: str) -> None:
        assert key == "ai_recognition_config"
        self.raw = None
        self.deleted = True


@pytest.mark.asyncio
async def test_provider_config_roundtrip_and_invalid_payload_fallback() -> None:
    store = FakeConfigStore()
    service = AIProviderService(store)

    assert (await service.get_config()).enabled is False
    await service.save_config(AIConfig(enabled=True, model="test-model", api_key="secret"))
    assert store.saved is not None and store.saved[1] is True
    assert (await service.get_config()).model == "test-model"

    store.raw = "not-json"
    assert (await service.get_config()).enabled is False
    await service.clear_config()
    assert store.deleted is True


@pytest.mark.asyncio
async def test_provider_returns_explanations_before_calling_remote_service() -> None:
    store = FakeConfigStore(AIConfig().model_dump_json())
    service = AIProviderService(store)

    disabled = await service.recognize(file_path="/media/episode.mkv", evidence={}, candidates=[])
    assert disabled.reason == "AI 辅助识别未启用"

    store.raw = AIConfig(enabled=True).model_dump_json()
    incomplete = await service.recognize(
        file_path="/media/episode.mkv", evidence={}, candidates=[]
    )
    assert incomplete.reason == "AI 配置不完整"


class FakeResponse:
    def __init__(self, body: dict[str, Any]) -> None:
        self.body = body

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self.body


class FakeClient:
    response_body: dict[str, Any] = {}
    last_url: str = ""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.kwargs = kwargs

    async def __aenter__(self) -> "FakeClient":
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    async def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.__class__.last_url = url
        assert kwargs["json"]["temperature"] == 0
        return FakeResponse(self.response_body)


@pytest.mark.asyncio
async def test_provider_validates_candidate_and_confidence(monkeypatch: pytest.MonkeyPatch) -> None:
    import server.application.ai_provider_service as provider_module

    config = AIConfig(
        enabled=True,
        model="test-model",
        api_key="secret",
        base_url="https://example.test/v1",
        auto_apply_threshold=0.95,
    )
    store = FakeConfigStore(config.model_dump_json())
    service = AIProviderService(store)
    FakeClient.response_body = {
        "choices": [{
            "message": {
                "content": (
                    "```json\n"
                    '{"title":"作品","search_titles":["作品"],'
                    '"season":1,"episode":2,"selected_candidate_id":999,'
                    '"confidence":0.8,"needs_confirmation":false}'
                    "\n```"
                )
            }
        }]
    }
    monkeypatch.setattr(provider_module.httpx, "AsyncClient", FakeClient)

    result = await service.recognize(
        file_path="/media/作品 S01E02.mkv",
        evidence={"parser": "standard"},
        candidates=[AICandidate(id=1, title="作品")],
    )

    assert FakeClient.last_url == "https://example.test/v1/chat/completions"
    assert result.selected_candidate_id is None
    assert result.needs_confirmation is True
    assert result.confidence == 0.5
    assert result.evidence == {"parser": "standard"}
    assert any("候选不在" in warning for warning in result.warnings)


@pytest.mark.asyncio
async def test_provider_wraps_http_and_malformed_responses(monkeypatch: pytest.MonkeyPatch) -> None:
    import server.application.ai_provider_service as provider_module

    config = AIConfig(enabled=True, model="test-model", api_key="secret")
    service = AIProviderService(FakeConfigStore(config.model_dump_json()))

    class FailingClient(FakeClient):
        async def post(self, url: str, **kwargs: Any) -> FakeResponse:
            raise httpx.ConnectError("offline")

    monkeypatch.setattr(provider_module.httpx, "AsyncClient", FailingClient)
    with pytest.raises(AIProviderError, match="AI 请求失败"):
        await service.recognize(file_path="/media/episode.mkv", evidence={}, candidates=[])

    FakeClient.response_body = {"choices": []}
    monkeypatch.setattr(provider_module.httpx, "AsyncClient", FakeClient)
    with pytest.raises(AIProviderError, match="响应缺少"):
        await service.recognize(file_path="/media/episode.mkv", evidence={}, candidates=[])

    with pytest.raises(AIProviderError, match="JSON 格式无效"):
        AIProviderService._json_from_response("[]")
