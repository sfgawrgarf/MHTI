"""Configuration API routes."""

from pydantic import BaseModel
from fastapi import APIRouter, Depends

from server.core.auth import require_auth
from server.core.container import get_config_service, get_p115_service, get_tmdb_service
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
    ProxyType,
    SUPPORTED_LANGUAGES,
)
from server.models.organize import OrganizeConfig
from server.models.download import DownloadConfig
from server.models.template import NamingTemplate
from server.models.watcher import (
    WatcherConfig,
    WatcherConfigRequest,
    WatcherConfigResponse,
    WatchedFolderCreate,
)
from server.models.nfo import NfoConfig
from server.models.system import SystemConfig
from server.services.config_service import ConfigService
from server.services.p115_service import P115Service
from server.services.tmdb_service import TMDBService
from server.api.watcher import get_watcher_service

router = APIRouter(prefix="/api/config", tags=["config"], dependencies=[Depends(require_auth)])


class Cloud115LoginRequest(BaseModel):
    """115 QR login request payload."""

    app: str = "alipaymini"


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
    config = WatcherConfig(
        enabled=request.enabled,
        mode=request.mode,
        performance_mode=request.performance_mode,
        watch_dirs=request.watch_dirs,
    )
    await config_service.save_watcher_config(config)

    # 同步 watch_dirs 到 watched_folders 表并启动/停止服务
    watcher_service = get_watcher_service()

    if request.enabled and request.watch_dirs:
        # 获取现有的监控目录
        existing_folders, _ = await watcher_service.list_folders()
        existing_paths = {f.path for f in existing_folders}

        # 添加新目录（自动识别 115 路径）
        for dir_path in request.watch_dirs:
            if dir_path not in existing_paths:
                create_req = WatchedFolderCreate(
                    path=dir_path,
                    enabled=True,
                    mode=request.mode,
                )
                # 路径以 /115网盘/ 开头 → 自动设为 115 provider + 解析 file_id
                if dir_path.startswith("/115网盘"):
                    create_req.provider = "115"
                    try:
                        from server.services.p115_service import P115Service
                        p115_svc = P115Service(config_service)
                        cfg = await config_service.get_115_config()
                        if cfg.is_logged_in:
                            client = await p115_svc._load_p115_client_with_config(cfg)
                            normalized = p115_svc._normalize_virtual_path(dir_path)
                            dir_id = await p115_svc._resolve_directory_id(
                                client=client, path=normalized, file_id=None,
                            )
                            create_req.file_id = str(dir_id)
                    except Exception:
                        pass  # 解析失败仍创建（后续轮询时会用路径扫描）
                await watcher_service.create_folder(create_req)

        # 删除不在列表中的目录
        for folder in existing_folders:
            if folder.path not in request.watch_dirs:
                await watcher_service.delete_folder(folder.id)

        # 启动监控服务
        await watcher_service.start()
    else:
        # 停止监控服务
        await watcher_service.stop()

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
