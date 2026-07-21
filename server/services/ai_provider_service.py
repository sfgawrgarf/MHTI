"""OpenAI-compatible provider used only for structured recognition suggestions."""

import json
from pathlib import Path
from typing import Any

import httpx

from server.models.ai import AIConfig, AICandidate, AIRecognitionResult
from server.services.config_service import ConfigService

AI_CONFIG_KEY = "ai_recognition_config"


class AIProviderError(ValueError):
    """Raised when an AI provider cannot return a usable structured suggestion."""


class AIProviderService:
    def __init__(self, config_service: ConfigService) -> None:
        self.config_service = config_service

    async def get_config(self) -> AIConfig:
        raw = await self.config_service.get(AI_CONFIG_KEY, encrypted=True)
        if not raw:
            return AIConfig()
        try:
            return AIConfig.model_validate_json(raw)
        except ValueError:
            return AIConfig()

    async def save_config(self, config: AIConfig) -> None:
        await self.config_service.set(AI_CONFIG_KEY, config.model_dump_json(), encrypted=True)

    async def clear_config(self) -> None:
        await self.config_service.delete(AI_CONFIG_KEY)

    @staticmethod
    def _json_from_response(content: str) -> dict[str, Any]:
        cleaned = content.strip()
        fence = chr(96) * 3
        if cleaned.startswith(fence):
            cleaned = cleaned.split("\n", 1)[-1]
        if cleaned.endswith(fence):
            cleaned = cleaned[:-3]
        try:
            data = json.loads(cleaned.strip())
        except json.JSONDecodeError as exc:
            raise AIProviderError("AI 返回的内容不是 JSON") from exc
        if not isinstance(data, dict):
            raise AIProviderError("AI 返回的 JSON 格式无效")
        return data

    async def recognize(
        self,
        *,
        file_path: str,
        evidence: dict[str, Any],
        candidates: list[AICandidate],
    ) -> AIRecognitionResult:
        config = await self.get_config()
        if not config.enabled:
            return AIRecognitionResult(
                reason="AI 辅助识别未启用",
                warnings=["可在设置页配置 OpenAI 兼容服务后重试"],
                evidence=evidence,
            )
        if not config.api_key or not config.model:
            return AIRecognitionResult(
                reason="AI 配置不完整",
                warnings=["请填写模型名和 API Key"],
                evidence=evidence,
            )

        prompt = {
            "task": "根据媒体文件证据在候选中选择最可能的剧集，并提取季和集。",
            "rules": [
                "仅根据提供的证据推断；不能编造候选之外的 ID。",
                "无法可靠确定时，confidence 必须低于 0.75 并设置 needs_confirmation=true。",
                "返回严格 JSON：title, season, episode, selected_candidate_id, confidence, reason, warnings, needs_confirmation。",
            ],
            "file_path_basename": Path(file_path).name,
            "evidence": evidence,
            "candidates": [candidate.model_dump() for candidate in candidates],
        }
        payload = {
            "model": config.model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": "你是谨慎的媒体文件识别助手，只返回 JSON。"},
                {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
            ],
        }
        try:
            async with httpx.AsyncClient(timeout=config.timeout_seconds) as client:
                response = await client.post(
                    config.base_url.rstrip("/") + "/chat/completions",
                    headers={"Authorization": "Bearer " + config.api_key},
                    json=payload,
                )
                response.raise_for_status()
                body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise AIProviderError(f"AI 请求失败: {exc}") from exc

        try:
            data = self._json_from_response(body["choices"][0]["message"]["content"])
            result = AIRecognitionResult.model_validate(data)
        except (KeyError, IndexError, ValueError, TypeError) as exc:
            raise AIProviderError("AI 响应缺少可用的识别结果") from exc

        valid_ids = {str(candidate.id) for candidate in candidates}
        if result.selected_candidate_id is not None and str(result.selected_candidate_id) not in valid_ids:
            result.selected_candidate_id = None
            result.warnings.append("AI 返回的候选不在本次搜索结果中，已忽略")
            result.needs_confirmation = True
            result.confidence = min(result.confidence, 0.5)
        result.confidence = max(0.0, min(1.0, result.confidence))
        if result.confidence < config.auto_apply_threshold:
            result.needs_confirmation = True
        result.evidence = evidence
        return result
