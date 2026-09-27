"""AI recognition and media-version data models."""

from enum import Enum
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator


class VersionPolicy(str, Enum):
    COEXIST = "coexist"
    PREFER_BEST = "prefer_best"
    SKIP = "skip"
    ARCHIVE = "archive"


class AIUsageMode(str, Enum):
    """Control when the configured AI participates in a scrape."""

    ASSIST_USE = "assist_use"
    FORCE_USE = "force_use"


def _validate_base_url(value: str) -> str:
    """Validate an OpenAI-compatible endpoint without restricting local hosts."""
    parsed = urlsplit(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("AI 服务地址必须是带主机名的 HTTP(S) URL")
    if parsed.username or parsed.password:
        raise ValueError("AI 服务地址不能包含认证信息")
    if parsed.fragment:
        raise ValueError("AI 服务地址不能包含片段")
    return value.strip().rstrip("/")


class AIConfig(BaseModel):
    enabled: bool = False
    usage_mode: AIUsageMode = AIUsageMode.ASSIST_USE
    base_url: str = "https://api.openai.com/v1"
    model: str = ""
    timeout_seconds: int = Field(default=30, ge=5, le=180)
    auto_apply_threshold: float = Field(default=0.92, ge=0.0, le=1.0)
    version_policy: VersionPolicy = VersionPolicy.COEXIST
    api_key: str = ""

    _validate_url = field_validator("base_url")(_validate_base_url)


class AIConfigUpdate(BaseModel):
    enabled: bool = False
    usage_mode: AIUsageMode = AIUsageMode.ASSIST_USE
    base_url: str = "https://api.openai.com/v1"
    model: str = ""
    timeout_seconds: int = Field(default=30, ge=5, le=180)
    auto_apply_threshold: float = Field(default=0.92, ge=0.0, le=1.0)
    version_policy: VersionPolicy = VersionPolicy.COEXIST
    api_key: str | None = None

    _validate_url = field_validator("base_url")(_validate_base_url)


class AIConfigResponse(BaseModel):
    enabled: bool
    usage_mode: AIUsageMode
    base_url: str
    model: str
    timeout_seconds: int
    auto_apply_threshold: float
    version_policy: VersionPolicy
    has_api_key: bool


class AICandidate(BaseModel):
    id: int | str
    title: str
    original_title: str | None = None
    year: int | None = None
    overview: str | None = None
    source: str = "tmdb"


class AIRecognitionRequest(BaseModel):
    file_path: str
    candidates: list[AICandidate] = Field(default_factory=list, max_length=100)


class AIRecognitionResult(BaseModel):
    title: str | None = None
    search_titles: list[str] = Field(default_factory=list, max_length=10)
    season: int | None = None
    episode: int | None = None
    selected_candidate_id: int | str | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reason: str = ""
    warnings: list[str] = Field(default_factory=list, max_length=20)
    needs_confirmation: bool = True
    evidence: dict[str, Any] = Field(default_factory=dict)


class VersionPreviewRequest(BaseModel):
    file_path: str
    tmdb_id: int
    season: int = Field(ge=0)
    episode: int = Field(ge=0)
    title: str | None = None
    policy: VersionPolicy | None = None


class VersionPreview(BaseModel):
    identity_key: str
    source_fingerprint: str
    quality_score: int
    quality_labels: list[str] = Field(default_factory=list)
    action: str
    reason: str
    existing_versions: list[dict[str, Any]] = Field(default_factory=list)


class VersionRecordRequest(VersionPreviewRequest):
    target_path: str | None = None
