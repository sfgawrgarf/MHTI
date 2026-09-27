"""监控配置用例 - 原子保存全局配置并同步监控目录快照。"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime

from server.common.path_security import validate_media_directory
from server.domain.integration.p115_service import P115Service
from server.domain.system.config_service import ConfigService
from server.models.storage import is_p115_virtual_path
from server.models.watcher import WatchedFolder, WatcherConfig, WatcherMode
from server.application.watcher_service import WatcherService

logger = logging.getLogger(__name__)

DEFAULT_WATCH_SCAN_INTERVAL_SECONDS = 60
PERFORMANCE_WATCH_SCAN_INTERVAL_SECONDS = 300


def effective_watcher_mode(path: str, requested_mode: WatcherMode) -> WatcherMode:
    """Map a global mode to one supported by the selected storage provider."""
    is_p115 = is_p115_virtual_path(path)
    if is_p115 and requested_mode == WatcherMode.REALTIME:
        return WatcherMode.COMPAT
    if not is_p115 and requested_mode == WatcherMode.EVENT:
        return WatcherMode.COMPAT
    return requested_mode


def watch_scan_interval(performance_mode: bool) -> int:
    """Return the scan interval selected by the performance profile."""
    return (
        PERFORMANCE_WATCH_SCAN_INTERVAL_SECONDS
        if performance_mode
        else DEFAULT_WATCH_SCAN_INTERVAL_SECONDS
    )


class WatcherConfigUseCase:
    """Validate and atomically apply the global watcher configuration."""

    def __init__(
        self,
        config_service: ConfigService,
        watcher_service: WatcherService,
        p115_service: P115Service,
    ) -> None:
        self._config_service = config_service
        self._watcher_service = watcher_service
        self._p115_service = p115_service

    async def _build_desired_folders(self, config: WatcherConfig) -> list[WatchedFolder]:
        existing_folders, _ = await self._watcher_service.list_folders()
        existing_by_path = {folder.path: folder for folder in existing_folders}
        scan_interval = watch_scan_interval(config.performance_mode)
        desired: list[WatchedFolder] = []
        p115_client = None

        for requested_path in config.watch_dirs:
            path = requested_path
            provider = "115" if is_p115_virtual_path(path) else "local"
            existing = existing_by_path.get(path)
            file_id = existing.file_id if existing is not None else None

            if config.enabled:
                if provider == "local":
                    path = str(validate_media_directory(path))
                    existing = existing or existing_by_path.get(path)
                    if existing is not None:
                        file_id = existing.file_id
                else:
                    try:
                        p115_config = await self._config_service.get_115_config()
                        if not p115_config.is_logged_in:
                            raise RuntimeError("115 尚未登录")
                        if p115_client is None:
                            p115_client = await self._p115_service._load_p115_client_with_config(
                                p115_config
                            )
                        normalized = self._p115_service._normalize_virtual_path(path)
                        resolved_id = await self._p115_service._resolve_directory_id(
                            client=p115_client,
                            path=normalized,
                            file_id=None,
                        )
                        file_id = str(resolved_id)
                    except Exception as exc:
                        raise ValueError(f"无法访问 115 监控目录: {path}") from exc

            desired.append(
                WatchedFolder(
                    id=existing.id if existing else str(uuid.uuid4())[:8],
                    path=path,
                    enabled=config.enabled,
                    mode=effective_watcher_mode(path, config.mode),
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
        return desired

    async def save_and_sync(self, config: WatcherConfig) -> WatcherConfig:
        """Apply the watcher snapshot and restore it if activation fails."""
        existing_folders, _ = await self._watcher_service.list_folders()
        old_config = await self._config_service.get_watcher_config()
        was_running = getattr(self._watcher_service, "_running", False) is True
        desired_folders = await self._build_desired_folders(config)
        effective_config = config.model_copy(
            update={"watch_dirs": [folder.path for folder in desired_folders]}
        )

        try:
            await self._watcher_service.stop(require_clean=True)
            await self._watcher_service.replace_folders(desired_folders)
            await self._config_service.save_watcher_config(effective_config)
            if effective_config.enabled and desired_folders:
                await self._watcher_service.start()
        except Exception as exc:
            logger.exception("保存监控配置失败，正在恢复旧配置")
            rollback_succeeded = False
            try:
                await self._watcher_service.stop(require_clean=True)
                await self._watcher_service.replace_folders(existing_folders)
                await self._config_service.save_watcher_config(old_config)
                if was_running:
                    await self._watcher_service.start()
                rollback_succeeded = True
            except Exception:
                logger.exception("监控配置回滚失败")
            if rollback_succeeded:
                raise RuntimeError("监控配置保存失败，已恢复原配置") from exc
            raise RuntimeError("监控配置保存且回滚失败，请检查服务日志") from exc
        return effective_config
