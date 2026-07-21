"""AI recognition and media-version data models."""

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class VersionPolicy(str, Enum):
    COEXIST = "coexist"
    PREFER_BEST = "prefer_best"
    SKIP = "skip"
    ARCHIVE = "archive"


class AIConfig(BaseModel):
    enabled: bool = False
    base_url: str = "https://api.openai.com/v1"
    model: str = ""
    timeout_seconds: int = Field(default=30, ge=5, le=180)
    auto_apply_threshold: float = Field(default=0.92, ge=0.0, le=1.0)
    version_policy: VersionPolicy = VersionPolicy.COEXIST
    api_key: str = ""


class AIConfigUpdate(BaseModel):
    enabled: bool = False
    base_url: str = "https://api.openai.com/v1"
    model: str = ""
    timeout_seconds: int = Field(default=30, ge=5, le=180)
    auto_apply_threshold: float = Field(default=0.92, ge=0.0, le=1.0)
    version_policy: VersionPolicy = VersionPolicy.COEXIST
    api_key: str | None = None


class AIConfigResponse(BaseModel):
    enabled: bool
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
    candidates: list[AICandidate] = Field(default_factory=list)


class AIRecognitionResult(BaseModel):
    title: str | None = None
    season: int | None = None
    episode: int | None = None
    selected_candidate_id: int | str | None = None
    confidence: float = 0.0
    reason: str = ""
    warnings: list[str] = Field(default_factory=list)
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
