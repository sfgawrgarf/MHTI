"""Emby integration data models."""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel


class ConflictType(str, Enum):
    """冲突类型枚举"""

    NO_CONFLICT = "no_conflict"  # 无冲突
    EPISODE_EXISTS = "episode_exists"  # 集已存在
    SERIES_EXISTS = "series_exists"  # 剧集存在但集不同
    CHECK_FAILED = "check_failed"  # 冲突检查不可用，禁止继续输出


class EmbyConfig(BaseModel):
    """Emby 配置模型"""

    enabled: bool = False  # 是否启用冲突检查
    server_url: str = ""  # 服务器地址
    api_key: str = ""  # API 密钥 (加密存储)
    user_id: str = ""  # 用户 ID (可选)
    library_ids: list[str] = []  # 要检查的媒体库 ID 列表
    check_before_scrape: bool = True  # 刮削前检查
    timeout: int = 10  # 请求超时 (秒)


class EmbyConfigRequest(BaseModel):
    """保存 Emby 配置请求"""

    enabled: bool
    server_url: str
    api_key: str
    user_id: str = ""
    library_ids: list[str] = []
    check_before_scrape: bool = True
    timeout: int = 10


class EmbyConfigResponse(BaseModel):
    """Emby 配置响应 (不返回 API Key)"""

    enabled: bool
    server_url: str
    has_api_key: bool
    user_id: str
    library_ids: list[str]
    check_before_scrape: bool
    timeout: int


class EmbyStatus(BaseModel):
    """Emby 连接状态"""

    is_configured: bool
    is_connected: bool | None = None
    server_name: str | None = None
    server_version: str | None = None
    last_checked: datetime | None = None
    error_message: str | None = None


class EmbyLibrary(BaseModel):
    """Emby 媒体库信息"""

    id: str
    name: str
    type: str  # "tvshows", "movies", etc.
    item_count: int = 0


class EmbyTestResponse(BaseModel):
    """测试连接响应"""

    success: bool
    message: str
    server_name: str | None = None
    server_version: str | None = None
    libraries: list[EmbyLibrary] = []
    latency_ms: int | None = None


class EmbySeriesMatch(BaseModel):
    """Emby 中匹配的剧集信息"""

    id: str
    name: str
    year: int | None = None
    path: str | None = None
    tmdb_id: int | None = None


class EmbyEpisodeMatch(BaseModel):
    """Emby 中匹配的集信息"""

    id: str
    name: str
    season: int
    episode: int
    path: str | None = None
    series_id: str
    series_name: str


class ConflictCheckRequest(BaseModel):
    """冲突检查请求"""

    series_name: str
    tmdb_id: int | None = None
    season: int
    episode: int


class ConflictCheckResult(BaseModel):
    """冲突检查结果"""

    conflict_type: ConflictType
    message: str | None = None
    existing_series: EmbySeriesMatch | None = None
    existing_episode: EmbyEpisodeMatch | None = None
