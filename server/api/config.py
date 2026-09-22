"""Configuration API routes."""

import logging
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from server.core.auth import require_auth
from server.core.container import get_config_service, get_p115_service, get_tmdb_service
from server.core.log_security import safe_log_value
from server.core.path_security import validate_media_path
from server.models.cloud_115 import Cloud115QrSession, Cloud115QrStatus, Cloud115Status
from server.models.config import (
    ApiTokenSaveRequest,
    ApiTokenSaveResponse,
    ApiTokenStatus,
    LanguageConfig,
    LanguageConfigRequest,
    LanguageConfigResponse,
    ProxyConfig,
    ProxyConfigRequest,
    ProxyConfigResponse,
    ProxyTestResponse,
    SUPPORTED_LANGUAGES,
)
from server.models.download import DownloadConfig
from server.models.nfo import NfoConfig
from server.models.organize import OrganizeConfig
from server.models.storage import is_p115_virtual_path
from server.models.system import SystemConfig
from server.models.template import NamingTemplate
from server.models.watcher import (
    WatcherConfig,
    WatcherConfigRequest,
    WatcherConfigResponse,
    WatchedFolder,
    WatcherMode,
)
from server.services.config_service import ConfigService
from server.services.p115_service import P115Service
from server.api.watcher import get_watcher_service
from server.services.tmdb_service import TMDBService

router = APIRouter(prefix="/api/config", tags=["config"], dependencies=[Depends(require_auth)])
logger = logging.getLogger(__name__)

DEFAULT_WATCH_SCAN_INTERVAL_SECONDS = 60
PERFORMANCE_WATCH_SCAN_INTERVAL_SECONDS = 300


class Cloud115LoginRequest(BaseModel):
    """115 QR login request payload."""

    app: str = "alipaymini"


def _effective_watcher_mode(path: str, requested_mode: WatcherMode) -> WatcherMode:
    """Map a global mode to one supported by the selected storage provider."""
    is_p115 = is_p115_virtual_path(path)
    if is_p115 and requested_mode == WatcherMode.REALTIME:
        return WatcherMode.COMPAT
    if not is_p115 and requested_mode == WatcherMode.EVENT:
        return WatcherMode.COMPAT
    return requested_mode


def _watch_scan_interval(performance_mode: bool) -> int:
    return (
        PERFORMANCE_WATCH_SCAN_INTERVAL_SECONDS
        if performance_mode
        else DEFAULT_WATCH_SCAN_INTERVAL_SECONDS
    )


# ========== Proxy Configuration ==========


@router.get("/proxy", response_model=ProxyConfigResponse)
async def get_proxy_config(
    config_service: ConfigService = Depends(get_config_service),
) -> ProxyConfigResponse:
    """Get current proxy configuration."""
    config = await config_service.get_proxy_config()
    return ProxyConfigResponse(
        type=config.type,
        host=config.host,
        port=config.port,
        has_auth=bool(config.username and config.password),
    )


@router.put("/proxy", response_model=ProxyConfigResponse)
async def save_proxy_config(
    request: ProxyConfigRequest,
    config_service: ConfigService = Depends(get_config_service),
) -> ProxyConfigResponse:
    """Save proxy configuration."""
    config = ProxyConfig(
        type=request.type,
        host=request.host,
        port=request.port,
        username=request.username,
        password=request.password,
    )
    await config_service.save_proxy_config(config)
    return ProxyConfigResponse(
        type=config.type,
        host=config.host,
        port=config.port,
        has_auth=bool(config.username and config.password),
    )


@router.delete("/proxy")
async def delete_proxy_config(
    config_service: ConfigService = Depends(get_config_service),
) -> dict:
    """Delete proxy configuration."""
    deleted = await config_service.delete_proxy_config()
    return {"success": deleted, "message": "代理配置已删除" if deleted else "无代理配置"}


@router.post("/proxy/test", response_model=ProxyTestResponse)
async def test_proxy(
    request: ProxyConfigRequest | None = None,
    tmdb_service: TMDBService = Depends(get_tmdb_service),
) -> ProxyTestResponse:
    """Test proxy connection to TMDB."""
    proxy_url = None
    if request is not None:
        proxy_url = ProxyConfig(
            type=request.type,
            host=request.host,
            port=request.port,
            username=request.username,
            password=request.password,
        ).get_url()

    success, message, latency = await tmdb_service.test_proxy(proxy_url)
    return ProxyTestResponse(
        success=success,
        message=message,
        latency_ms=latency,
    )


# ========== Language Configuration ==========


@router.get("/language", response_model=LanguageConfigResponse)
async def get_language_config(
    config_service: ConfigService = Depends(get_config_service),
) -> LanguageConfigResponse:
    """Get current language configuration."""
    config = await config_service.get_language_config()
    return LanguageConfigResponse(
        primary=config.primary,
        fallback=config.fallback,
        supported=SUPPORTED_LANGUAGES,
    )


@router.put("/language", response_model=LanguageConfigResponse)
async def save_language_config(
    request: LanguageConfigRequest,
    config_service: ConfigService = Depends(get_config_service),
) -> LanguageConfigResponse:
    """Save language configuration."""
    config = LanguageConfig(primary=request.primary, fallback=request.fallback)
    await config_service.save_language_config(config)
    return LanguageConfigResponse(
        primary=config.primary,
        fallback=config.fallback,
        supported=SUPPORTED_LANGUAGES,
    )


# ========== API Token Configuration ==========


@router.post("/api-token", response_model=ApiTokenSaveResponse)
async def save_api_token(
    request: ApiTokenSaveRequest,
    tmdb_service: TMDBService = Depends(get_tmdb_service),
) -> ApiTokenSaveResponse:
    """
    Save and verify TMDB API token.

    The token will be verified first, and only saved if valid.
    """
    status = await tmdb_service.save_and_verify_api_token(request.token)

    if status.is_configured and status.is_valid:
        return ApiTokenSaveResponse(
            success=True,
            message="API Token 保存并验证成功",
            status=status,
        )
    else:
        return ApiTokenSaveResponse(
            success=False,
            message=status.error_message or "API Token 验证失败",
            status=status,
        )


@router.get("/api-token/status", response_model=ApiTokenStatus)
async def get_api_token_status(
    tmdb_service: TMDBService = Depends(get_tmdb_service),
) -> ApiTokenStatus:
    """Get current TMDB API token status."""
    return await tmdb_service.get_api_token_status()


@router.delete("/api-token")
async def delete_api_token(
    tmdb_service: TMDBService = Depends(get_tmdb_service),
) -> dict:
    """Delete stored TMDB API token."""
    deleted = await tmdb_service.delete_api_token()

    if deleted:
        return {"success": True, "message": "API Token 已删除"}
    else:
        return {"success": False, "message": "未配置 API Token"}


# ========== Organize Configuration ==========


@router.get("/organize", response_model=OrganizeConfig)
async def get_organize_config(
    config_service: ConfigService = Depends(get_config_service),
) -> OrganizeConfig:
    """Get current organize configuration."""
    return await config_service.get_organize_config()


@router.put("/organize", response_model=OrganizeConfig)
async def save_organize_config(
    request: OrganizeConfig,
    config_service: ConfigService = Depends(get_config_service),
) -> OrganizeConfig:
    """Save organize configuration."""
    await config_service.save_organize_config(request)
    return request


# ========== Download Configuration ==========


@router.get("/download", response_model=DownloadConfig)
async def get_download_config(
    config_service: ConfigService = Depends(get_config_service),
) -> DownloadConfig:
    """Get current download configuration."""
    return await config_service.get_download_config()


@router.put("/download", response_model=DownloadConfig)
async def save_download_config(
    request: DownloadConfig,
    config_service: ConfigService = Depends(get_config_service),
) -> DownloadConfig:
    """Save download configuration."""
    await config_service.save_download_config(request)
    return request


# ========== Naming Configuration ==========


@router.get("/naming", response_model=NamingTemplate)
async def get_naming_config(
    config_service: ConfigService = Depends(get_config_service),
) -> NamingTemplate:
    """Get current naming template configuration."""
    return await config_service.get_naming_config()


@router.put("/naming", response_model=NamingTemplate)
async def save_naming_config(
    request: NamingTemplate,
    config_service: ConfigService = Depends(get_config_service),
) -> NamingTemplate:
    """Save naming template configuration."""
    await config_service.save_naming_config(request)
    return request


# ========== Watcher Configuration ==========


@router.get("/watcher-config", response_model=WatcherConfigResponse)
async def get_watcher_config(
    config_service: ConfigService = Depends(get_config_service),
) -> WatcherConfigResponse:
    """Get current watcher configuration."""
    config = await config_service.get_watcher_config()
    return WatcherConfigResponse(
        enabled=config.enabled,
        mode=config.mode,
        performance_mode=config.performance_mode,
        watch_dirs=config.watch_dirs,
    )


@router.put("/watcher-config", response_model=WatcherConfigResponse)
async def save_watcher_config(
    request: WatcherConfigRequest,
    config_service: ConfigService = Depends(get_config_service),
) -> WatcherConfigResponse:
    """Save watcher configuration and sync to watcher service."""
    watcher_service = get_watcher_service()
    existing_folders, _ = await watcher_service.list_folders()
    old_config = await config_service.get_watcher_config()
    was_running = getattr(watcher_service, "_running", False) is True
    existing_by_path = {folder.path: folder for folder in existing_folders}
    desired_folders: list[WatchedFolder] = []

    scan_interval = _watch_scan_interval(request.performance_mode)
    p115_service: P115Service | None = None
    p115_client = None
    for requested_dir_path in request.watch_dirs:
        dir_path = requested_dir_path
        provider = "115" if is_p115_virtual_path(dir_path) else "local"
        existing = existing_by_path.get(dir_path)
        file_id: str | None = None
        if request.enabled:
            if provider == "local":
                try:
                    dir_path = str(
                        validate_media_path(
                            dir_path,
                            must_exist=True,
                            require_directory=True,
                        )
                    )
                except (OSError, RuntimeError, ValueError) as exc:
                    raise HTTPException(status_code=400, detail=str(exc)) from exc
                if existing is None:
                    existing = existing_by_path.get(dir_path)
            else:
                try:
                    if p115_service is None:
                        p115_service = P115Service(config_service)
                        p115_config = await config_service.get_115_config()
                        if not p115_config.is_logged_in:
                            raise RuntimeError("115 尚未登录")
                        p115_client = await p115_service._load_p115_client_with_config(
                            p115_config
                        )
                    normalized = p115_service._normalize_virtual_path(dir_path)
                    resolved_id = await p115_service._resolve_directory_id(
                        client=p115_client,
                        path=normalized,
                        file_id=None,
                    )
                    file_id = str(resolved_id)
                except Exception as exc:
                    # Both external fields are converted to bounded single-line values.
                    # codeql[py/log-injection]
                    logger.warning(
                        "115 监控目录预校验失败 path=%s: %s",
                        safe_log_value(dir_path),
                        safe_log_value(exc),
                    )
                    raise HTTPException(
                        status_code=400,
                        detail=f"无法访问 115 监控目录: {dir_path}",
                    ) from exc

        if existing is not None and file_id is None:
            file_id = existing.file_id

        desired_folders.append(
            WatchedFolder(
                id=existing.id if existing else str(uuid.uuid4())[:8],
                path=dir_path,
                enabled=request.enabled,
                mode=_effective_watcher_mode(dir_path, request.mode),
                scan_interval_seconds=scan_interval,
                file_stable_seconds=existing.file_stable_seconds if existing else 30,
                auto_scrape=existing.auto_scrape if existing else True,
                output_dir=existing.output_dir if existing else None,
                provider=provider,
                file_id=file_id,
                last_scan=existing.last_scan if existing else None,
                created_at=existing.created_at if existing else datetime.now(),
            )
        )

    config = WatcherConfig(
        enabled=request.enabled,
        mode=request.mode,
        performance_mode=request.performance_mode,
        watch_dirs=[folder.path for folder in desired_folders],
    )

    try:
        await watcher_service.stop(require_clean=True)
        await watcher_service.replace_folders(desired_folders)
        await config_service.save_watcher_config(config)
        if request.enabled and desired_folders:
            await watcher_service.start()
    except Exception as exc:
        logger.exception("保存监控配置失败，正在恢复旧配置")
        rollback_succeeded = False
        try:
            await watcher_service.stop(require_clean=True)
            await watcher_service.replace_folders(existing_folders)
            await config_service.save_watcher_config(old_config)
            if was_running:
                await watcher_service.start()
            rollback_succeeded = True
        except Exception:
            logger.exception("监控配置回滚失败")
        if isinstance(exc, HTTPException):
            raise
        detail = (
            "监控配置保存失败，已恢复原配置"
            if rollback_succeeded
            else "监控配置保存且回滚失败，请检查服务日志"
        )
        raise HTTPException(status_code=500, detail=detail) from exc

    return WatcherConfigResponse(
        enabled=config.enabled,
        mode=config.mode,
        performance_mode=config.performance_mode,
        watch_dirs=config.watch_dirs,
    )


# ========== NFO Configuration ==========


@router.get("/nfo", response_model=NfoConfig)
async def get_nfo_config(
    config_service: ConfigService = Depends(get_config_service),
) -> NfoConfig:
    """Get current NFO configuration."""
    return await config_service.get_nfo_config()


@router.put("/nfo", response_model=NfoConfig)
async def save_nfo_config(
    request: NfoConfig,
    config_service: ConfigService = Depends(get_config_service),
) -> NfoConfig:
    """Save NFO configuration."""
    await config_service.save_nfo_config(request)
    return request


# ========== System Configuration ==========


@router.get("/system", response_model=SystemConfig)
async def get_system_config(
    config_service: ConfigService = Depends(get_config_service),
) -> SystemConfig:
    """Get current system configuration."""
    return await config_service.get_system_config()


@router.put("/system", response_model=SystemConfig)
async def save_system_config(
    request: SystemConfig,
    config_service: ConfigService = Depends(get_config_service),
) -> SystemConfig:
    """Save system configuration."""
    await config_service.save_system_config(request)
    return request


# ========== 115 Cloud Login ==========


@router.get("/115", response_model=Cloud115Status)
async def get_115_status(
    p115_service: P115Service = Depends(get_p115_service),
) -> Cloud115Status:
    """Get current 115 login status."""
    return await p115_service.get_status()


@router.get("/115/devices")
async def get_115_devices(
    p115_service: P115Service = Depends(get_p115_service),
) -> dict:
    """List supported 115 login devices."""
    return {"items": [item.model_dump() for item in p115_service.list_login_devices()]}


@router.post("/115/login/qrcode", response_model=Cloud115QrSession)
async def start_115_qrcode_login(
    request: Cloud115LoginRequest,
    p115_service: P115Service = Depends(get_p115_service),
) -> Cloud115QrSession:
    """Create a 115 QR login session."""
    return await p115_service.start_qr_login(request.app)


@router.get("/115/login/status", response_model=Cloud115QrStatus)
async def get_115_qrcode_login_status(
    uid: str,
    app: str = "alipaymini",
    p115_service: P115Service = Depends(get_p115_service),
) -> Cloud115QrStatus:
    """Poll current 115 QR login status."""
    return await p115_service.poll_qr_login(uid, app)


@router.delete("/115/login")
async def delete_115_login(
    p115_service: P115Service = Depends(get_p115_service),
) -> dict:
    """Clear stored 115 login information."""
    await p115_service.clear_login_state()
    return {"success": True, "message": "115 登录信息已清除"}
