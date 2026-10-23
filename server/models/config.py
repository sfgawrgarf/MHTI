"""Configuration data models."""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel


class ProxyType(str, Enum):
    """Proxy type enumeration."""

    NONE = "none"
    HTTP = "http"
    SOCKS5 = "socks5"


class ProxyConfig(BaseModel):
    """Proxy configuration model."""

    type: ProxyType = ProxyType.NONE
    host: str = ""
    port: int = 0
    username: str | None = None
    password: str | None = None

    def get_url(self) -> str | None:
        """Get proxy URL for httpx."""
        if self.type == ProxyType.NONE or not self.host or not self.port:
            return None

        auth = ""
        if self.username and self.password:
            auth = f"{self.username}:{self.password}@"

        if self.type == ProxyType.HTTP:
            return f"http://{auth}{self.host}:{self.port}"
        elif self.type == ProxyType.SOCKS5:
            return f"socks5://{auth}{self.host}:{self.port}"
        return None


class ProxyConfigRequest(BaseModel):
    """Request model for saving proxy config."""

    type: ProxyType
    host: str = ""
    port: int = 0
    username: str | None = None
    password: str | None = None
    clear_auth: bool = False


class ProxyConfigResponse(BaseModel):
    """Response model for proxy config."""

    type: ProxyType
    host: str
    port: int
    has_auth: bool


class ProxyTestResponse(BaseModel):
    """Response model for proxy test."""

    success: bool
    message: str
    latency_ms: int | None = None


# 支持的语言列表
SUPPORTED_LANGUAGES = [
    ("zh-CN", "简体中文"),
    ("zh-TW", "繁体中文"),
    ("en-US", "English"),
    ("ja-JP", "日本語"),
]


class LanguageConfig(BaseModel):
    """Language configuration model."""

    primary: str = "zh-CN"
    fallback: list[str] = ["en-US"]

    def get_languages(self) -> list[str]:
        """Get ordered list of languages to try."""
        langs = [self.primary]
        for lang in self.fallback:
            if lang not in langs:
                langs.append(lang)
        return langs


class LanguageConfigRequest(BaseModel):
    """Request model for saving language config."""

    primary: str
    fallback: list[str] = []


class LanguageConfigResponse(BaseModel):
    """Response model for language config."""

    primary: str
    fallback: list[str]
    supported: list[tuple[str, str]]


class CookieStatus(BaseModel):
    """Cookie status information."""

    is_configured: bool
    is_valid: bool | None = None
    expires_at: datetime | None = None
    last_verified: datetime | None = None
    error_message: str | None = None


class CookieSaveRequest(BaseModel):
    """Request model for saving cookie."""

    cookie: str


class CookieSaveResponse(BaseModel):
    """Response model for saving cookie."""

    success: bool
    message: str
    status: CookieStatus


class CookieDeleteResponse(BaseModel):
    """Response model for deleting cookie."""

    success: bool
    message: str


# API Token 相关模型
class ApiTokenStatus(BaseModel):
    """API Token status information."""

    is_configured: bool
    is_valid: bool | None = None
    last_verified: datetime | None = None
    error_message: str | None = None
    # R18（成人内容）探测：True 已开启 / False 未开启 / None 无法判定（见 TMDBService.check_adult_access）
    adult_enabled: bool | None = None
    adult_message: str | None = None
    adult_checked_at: datetime | None = None


class ApiTokenSaveRequest(BaseModel):
    """Request model for saving API token."""

    token: str


class ApiTokenSaveResponse(BaseModel):
    """Response model for saving API token."""

    success: bool
    message: str
    status: ApiTokenStatus
