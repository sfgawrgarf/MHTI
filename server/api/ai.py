"""AI-assisted recognition and media version APIs."""

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from server.core.auth import require_auth
from server.core.container import get_config_service, get_parser_service, get_tmdb_service
from server.models.ai import (
    AIConfig,
    AIConfigResponse,
    AIConfigUpdate,
    AICandidate,
    AIRecognitionRequest,
    AIRecognitionResult,
    VersionPreview,
    VersionPreviewRequest,
    VersionRecordRequest,
)
from server.services.ai_provider_service import AIProviderError, AIProviderService
from server.services.config_service import ConfigService
from server.services.media_identity_service import MediaIdentityService
from server.services.parser_service import ParserService
from server.services.tmdb_service import TMDBService

router = APIRouter(prefix="/api/ai", tags=["ai"], dependencies=[Depends(require_auth)])


def get_ai_provider(
    config_service: ConfigService = Depends(get_config_service),
) -> AIProviderService:
    return AIProviderService(config_service)


@router.get("/config", response_model=AIConfigResponse)
async def get_ai_config(provider: AIProviderService = Depends(get_ai_provider)) -> AIConfigResponse:
    config = await provider.get_config()
    return AIConfigResponse(
        enabled=config.enabled,
        base_url=config.base_url,
        model=config.model,
        timeout_seconds=config.timeout_seconds,
        auto_apply_threshold=config.auto_apply_threshold,
        version_policy=config.version_policy,
        has_api_key=bool(config.api_key),
    )


@router.put("/config", response_model=AIConfigResponse)
async def save_ai_config(
    request: AIConfigUpdate,
    provider: AIProviderService = Depends(get_ai_provider),
) -> AIConfigResponse:
    old = await provider.get_config()
    config = AIConfig(
        **request.model_dump(exclude={"api_key"}),
        api_key=request.api_key if request.api_key is not None else old.api_key,
    )
    await provider.save_config(config)
    return AIConfigResponse(
        enabled=config.enabled,
        base_url=config.base_url,
        model=config.model,
        timeout_seconds=config.timeout_seconds,
        auto_apply_threshold=config.auto_apply_threshold,
        version_policy=config.version_policy,
        has_api_key=bool(config.api_key),
    )


@router.delete("/config")
async def clear_ai_config(provider: AIProviderService = Depends(get_ai_provider)) -> dict[str, bool]:
    await provider.clear_config()
    return {"success": True}


@router.post("/recognize", response_model=AIRecognitionResult)
async def recognize(
    request: AIRecognitionRequest,
    provider: AIProviderService = Depends(get_ai_provider),
    parser: ParserService = Depends(get_parser_service),
    tmdb_service: TMDBService = Depends(get_tmdb_service),
) -> AIRecognitionResult:
    path = Path(request.file_path)
    parsed = parser.parse(path.name, request.file_path)
    candidates = request.candidates
    if not candidates and parsed.series_name:
        try:
            response = await tmdb_service.search_series_by_api(parsed.series_name)
            candidates = [
                AICandidate(
                    id=item.id,
                    title=item.name,
                    original_title=item.original_name,
                    year=int(item.first_air_date[:4]) if item.first_air_date else None,
                    overview=item.overview,
                )
                for item in response.results[:10]
            ]
        except Exception:
            candidates = []
    evidence: dict[str, Any] = {
        "filename": path.name,
        "parsed_title": parsed.series_name,
        "parsed_season": parsed.season,
        "parsed_episode": parsed.episode,
        "parser_confidence": parsed.confidence,
        "suffix": path.suffix.lower(),
        "strm_content_fingerprint": MediaIdentityService.fingerprint(request.file_path)
        if path.suffix.lower() == ".strm" else None,
    }
    try:
        return await provider.recognize(
            file_path=request.file_path,
            evidence=evidence,
            candidates=candidates,
        )
    except AIProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/versions/preview", response_model=VersionPreview)
async def preview_version(
    request: VersionPreviewRequest,
    provider: AIProviderService = Depends(get_ai_provider),
) -> VersionPreview:
    policy = request.policy or (await provider.get_config()).version_policy
    return await MediaIdentityService().preview(
        file_path=request.file_path,
        tmdb_id=request.tmdb_id,
        season=request.season,
        episode=request.episode,
        policy=policy,
    )


@router.post("/versions/record", response_model=VersionPreview)
async def record_version(request: VersionRecordRequest) -> VersionPreview:
    return await MediaIdentityService().record(
        file_path=request.file_path,
        target_path=request.target_path,
        tmdb_id=request.tmdb_id,
        season=request.season,
        episode=request.episode,
        title=request.title,
    )
