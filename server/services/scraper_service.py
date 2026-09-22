"""Scraper service for orchestrating the scraping workflow.

该模块是刮削工作流的核心编排器，通过 Mixin 模式组织代码：
- ScraperConfigMixin: 配置管理
- ScraperMetadataMixin: 元数据处理（NFO、搜索结果）
- ScraperMediaMixin: 媒体文件处理（图片、字幕、Emby）
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import httpx

from server.core.exceptions import TMDBError
from server.core.log_security import safe_log_value
from server.core.path_security import PathSecurityError, validate_media_path
from server.services.file_operations import write_metadata_text
from server.services.file_io import run_file_io

from server.models.emby import ConflictType
from server.models.history import LogLevel, ScrapeLogEntry, ScrapeLogStep
from server.models.manual_job import ManualJobAdvancedSettings
from server.models.organize import OrganizeMode
from server.models.rename import RenameRequest
from server.models.scraper import (
    BatchScrapeRequest,
    BatchScrapeResponse,
    ScrapeByIdRequest,
    ScrapePreview,
    ScrapeRequest,
    ScrapeResult,
    ScrapeStatus,
)
from server.models.storage import StorageLocator, StorageProvider
from server.models.template import NamingTemplate
from server.models.tmdb import TMDBSearchResult, TMDBSeason, TMDBSeries
from server.models.ai import AICandidate, AIRecognitionResult, AIUsageMode
from server.services.ai_provider_service import AIProviderError, AIProviderService
from server.services.config_service import ConfigService
from server.services.emby_service import EmbyService
from server.services.image_service import ImageService
from server.services.media_alias_service import MediaAliasMatch, MediaAliasService
from server.services.nfo_service import NFOService
from server.services.parser_service import ParserService
from server.services.recognition_service import (
    EpisodeMatch,
    build_search_title_variants,
    extract_release_year_month,
    match_episode_from_titles,
    merge_search_results,
    select_series_candidate,
)
from server.services.rename_service import RenameService
from server.services.scraper_config import ScraperConfigMixin
from server.services.scraper_media import ScraperMediaMixin
from server.services.scraper_metadata import ScraperMetadataMixin
from server.services.subtitle_service import SubtitleService
from server.services.tmdb_service import TMDBService
from server.services.template_service import TemplateService

logger = logging.getLogger(__name__)

# 日志更新回调类型
LogUpdateCallback = Callable[[list[ScrapeLogStep]], Awaitable[None]]

# 整理模式中文名称映射
_MODE_NAMES = {
    OrganizeMode.COPY: "复制",
    OrganizeMode.MOVE: "移动",
    OrganizeMode.HARDLINK: "硬链接",
    OrganizeMode.SYMLINK: "软链接",
}


def _get_mode_name(mode: OrganizeMode | None) -> str:
    """获取整理模式的中文名称。"""
    if mode is None:
        return "移动"
    return _MODE_NAMES.get(mode, "移动")


def _can_auto_apply_ai_result(result: AIRecognitionResult) -> bool:
    """Return whether an AI result is safe to use in an automatic scrape.

    Low-confidence results are still useful for the manual recognition preview,
    but they must not alter the title or episode data consumed by a worker.
    """
    return not result.needs_confirmation


def _should_use_ai(
    usage_mode: AIUsageMode,
    *,
    has_confirmed_alias: bool,
    has_adult_candidates: bool,
) -> bool:
    """Return whether AI should run for the current scrape.

    Assist mode is a fallback for filenames that ordinary TMDB searching could
    not resolve. Force mode mirrors CMS's ``force_use`` behavior: every source
    without a confirmed local alias must pass through AI recognition.
    """
    if has_confirmed_alias:
        return False
    return usage_mode == AIUsageMode.FORCE_USE or not has_adult_candidates


def _resolve_task_output_preferences(
    settings: ManualJobAdvancedSettings | None,
    file_action: str | None,
) -> tuple[str | None, bool]:
    """Resolve task-level video conflict and subtitle behavior."""
    overwrite_video = bool(
        settings
        and not settings.use_global_organize
        and settings.overwrite_video
    )
    effective_file_action = file_action or ("overwrite" if overwrite_video else None)
    process_subtitles = settings is None or settings.process_subtitle
    return effective_file_action, process_subtitles


def _resolve_task_naming_template(
    settings: ManualJobAdvancedSettings | None,
) -> NamingTemplate | None:
    """Build a validated per-task template or safely fall back to global naming."""
    if settings is None or settings.use_global_naming:
        return None

    defaults = NamingTemplate()
    candidate = NamingTemplate(
        series_folder=settings.series_folder_template.strip()
        or defaults.series_folder,
        season_folder=settings.season_folder_template.strip()
        or defaults.season_folder,
        episode_file=settings.episode_file_template.strip()
        or defaults.episode_file,
    )
    validator = TemplateService()
    for template in (
        candidate.series_folder,
        candidate.season_folder,
        candidate.episode_file,
    ):
        if not validator.validate_template(template).valid:
            logger.warning(
                "Ignoring invalid naming override from a legacy task: %s",
                template,
            )
            return None
    return candidate


class _P115StorageProvider:
    """115 网盘文件输出适配层。"""

    def __init__(self, config_service: ConfigService) -> None:
        self._config_service = config_service

    async def _get_client(self) -> tuple[Any, str]:
        from server.services import p115_service as p115_service_module

        config = await self._config_service.get_115_config()
        if not config.is_logged_in or not config.cookies.strip():
            raise ValueError("请先登录 115 网盘")

        p115_module, _ = await p115_service_module._load_p115client()
        client = p115_service_module._create_p115_client(
            p115_module,
            cookies=config.cookies,
            app=config.app or p115_service_module.DEFAULT_APP,
        )
        return client, config.app or p115_service_module.DEFAULT_APP

    def _extract_response_id(
        self,
        response: Any,
        *,
        fallback_id: str | None = None,
        fallback_parent_id: str | None = None,
    ) -> dict[str, str | None]:
        """从 115 接口响应中提取目录 ID。"""
        payload = response.get("data", response) if isinstance(response, dict) else response
        if not isinstance(payload, dict):
            return {"id": fallback_id, "parent_id": fallback_parent_id}

        target_id = payload.get("cid") or payload.get("id") or payload.get("file_id") or fallback_id
        parent_id = payload.get("pid") or payload.get("parent_id") or fallback_parent_id
        return {
            "id": None if target_id in (None, "") else str(target_id),
            "parent_id": None if parent_id in (None, "") else str(parent_id),
        }

    @staticmethod
    def _ensure_operation_succeeded(response: Any, operation: str) -> None:
        """Reject provider responses that report failure without raising."""
        if isinstance(response, dict) and response.get("state") is False:
            message = response.get("error") or response.get("message") or response
            raise ValueError(f"115 {operation}失败: {message}")

    @staticmethod
    def _extract_file_id(response: Any) -> str | None:
        """Extract a copied file id from either a flat or nested response."""
        payload = response.get("data", response) if isinstance(response, dict) else response
        if not isinstance(payload, dict):
            return None
        value = payload.get("file_id") or payload.get("fid") or payload.get("id")
        return None if value in (None, "", "0") else str(value)

    async def ensure_directory(
        self,
        locator: StorageLocator,
        relative_path: str,
    ) -> dict[str, str | None]:
        """确保目标目录存在。

        用 ``fs_mkdir`` 逐层创建（不依赖 ``batch_makedir``，后者在 async 模式下
        存在协程未 await 的 bug，导致目录不会被实际创建）。

        注意：``fs_mkdir`` 对已存在目录返回 ``state:False`` 且不含 id，必须
        用 ``fs_files`` 查询该层目录的真实 id，否则后续文件会被移到错误目录。
        """
        base_id = locator.file_id or locator.parent_id
        if not base_id:
            raise ValueError("115 输出目录缺少 file_id")

        if not relative_path or relative_path == ".":
            return {"id": str(base_id), "parent_id": locator.parent_id}

        client, _ = await self._get_client()

        # 拆分相对路径，逐层创建
        parts = [p for p in relative_path.replace("\\", "/").split("/") if p and p != "."]
        current_pid = str(base_id)
        for name in parts:
            response = await client.fs_mkdir(name, pid=current_pid, async_=True)
            # fs_mkdir 成功时返回新目录 id
            new_id = None
            if isinstance(response, dict):
                if response.get("state"):
                    new_id = response.get("cid") or response.get("file_id") or response.get("id")
                # state:False 表示目录已存在 → 查询真实 id
            if not new_id:
                new_id = await self._find_subdir_id(client, current_pid, name)
            if not new_id:
                raise ValueError(f"115 创建目录失败或无法定位: {name}")
            current_pid = str(new_id)

        return {"id": current_pid, "parent_id": locator.parent_id}

    async def _find_subdir_id(
        self,
        client: Any,
        parent_pid: str,
        name: str,
    ) -> str | None:
        """在父目录下按名字查找子目录的 cid（fs_mkdir 命中已存在目录时用）。"""
        offset = 0
        page_size = 100
        while True:
            try:
                response = await client.fs_files(
                    {
                        "cid": parent_pid,
                        "offset": offset,
                        "limit": page_size,
                        "show_dir": 1,
                    },
                    async_=True,
                )
            except Exception as exc:
                # Every external field is converted to a bounded single-line value.
                # codeql[py/log-injection]
                logger.warning(
                    "115 子目录查询失败 parent_id=%s name=%s: %s",
                    safe_log_value(parent_pid),
                    safe_log_value(name),
                    safe_log_value(exc),
                    exc_info=True,
                )
                return None
            rows = response.get("data", []) if isinstance(response, dict) else []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                row_name = row.get("n") or row.get("name") or ""
                # 目录条目用 cid，文件用 fid
                cid = row.get("cid") or row.get("id")
                if row_name == name and cid and "fid" not in row:
                    return str(cid)
            if len(rows) < page_size:
                break
            offset += page_size
        return None

    async def rename(
        self,
        locator: StorageLocator,
        target_name: str,
        target_parent_id: str,
    ) -> dict[str, Any]:
        """移动并重命名文件。

        用 ``fs_rename`` + ``fs_move`` 组合实现，绕开 ``renamefile`` 内部的
        ``download_url`` 反查（某些文件反查会报 "index out of bounds"）。
        """
        if not locator.file_id:
            raise ValueError("115 源文件缺少 file_id")

        client, _ = await self._get_client()
        file_id = locator.file_id

        original_parent_id = locator.parent_id
        original_name = Path(locator.path).name

        # 先移动，避免目标目录不可用时把源目录中的文件提前改名。
        try:
            resp = await client.fs_move(file_id, pid=target_parent_id, async_=True)
            self._ensure_operation_succeeded(resp, "文件移动")
        except Exception as exc:
            raise ValueError(f"115 文件移动失败: {file_id} -> {target_parent_id}") from exc

        # 再改名。失败时尽最大努力移回原目录，避免留下不明位置的半成品。
        if target_name and target_name != original_name:
            try:
                rename_response = await client.fs_rename((file_id, target_name), async_=True)
                self._ensure_operation_succeeded(rename_response, "文件改名")
            except Exception as exc:
                rollback_error: Exception | None = None
                if original_parent_id:
                    try:
                        rollback = await client.fs_move(
                            file_id,
                            pid=original_parent_id,
                            async_=True,
                        )
                        self._ensure_operation_succeeded(rollback, "移动回滚")
                    except Exception as rollback_exc:  # pragma: no cover - provider edge
                        rollback_error = rollback_exc
                if rollback_error is not None:
                    raise ValueError(
                        "115 文件已移动到目标目录，但改名和回滚均失败；"
                        f"file_id={file_id}, target_parent_id={target_parent_id}: "
                        f"{rollback_error}"
                    ) from exc
                if not original_parent_id:
                    raise ValueError(
                        "115 文件已移动到目标目录但改名失败，且缺少原父目录 ID，"
                        f"无法自动回滚: file_id={file_id}"
                    ) from exc
                raise ValueError(f"115 文件改名失败，已移回原目录: {target_name}") from exc

        return {"response": resp, "file_id": file_id}

    async def copy(
        self,
        locator: StorageLocator,
        target_name: str,
        target_parent_id: str,
    ) -> dict[str, Any]:
        """复制文件到目标目录并改名。

        用 ``fs_copy`` + ``fs_rename`` 组合实现，绕开 ``copyfile`` 内部的
        ``download_url`` 反查。

        注意：``fs_copy`` 响应不含新文件 id，必须复制后在目标目录按源文件名
        查找新文件的 id，才能执行改名。
        """
        if not locator.file_id:
            raise ValueError("115 源文件缺少 file_id")

        client, _ = await self._get_client()
        file_id = locator.file_id
        source_name = Path(locator.path).name

        # 复制前先记录目标目录内的同名文件 ID。复制后只能用新增 ID 定位副本，
        # 不能把一个更早存在的同名文件误认为本次副本。
        before_ids = await self._find_file_ids_in_dir(client, target_parent_id, source_name)

        # 1. 复制到目标目录（保持原名）
        try:
            resp = await client.fs_copy(file_id, pid=target_parent_id, async_=True)
            self._ensure_operation_succeeded(resp, "文件复制")
        except Exception as exc:
            raise ValueError(f"115 文件复制失败: {file_id} -> {target_parent_id}") from exc

        # 2. 优先使用响应 ID；否则通过复制前后的 ID 差集定位，并为服务端的
        # 短暂最终一致性留出有限重试窗口。
        new_id = self._extract_file_id(resp)
        if not new_id:
            for attempt in range(3):
                after_ids = await self._find_file_ids_in_dir(
                    client,
                    target_parent_id,
                    source_name,
                )
                candidates = after_ids - before_ids
                if len(candidates) == 1:
                    new_id = next(iter(candidates))
                    break
                if len(candidates) > 1:
                    raise ValueError(
                        "115 复制后出现多个同名新文件，无法安全确认新副本 ID: "
                        f"{sorted(candidates)}"
                    )
                if attempt < 2:
                    await asyncio.sleep(0.2 * (attempt + 1))

        if not new_id:
            raise ValueError(
                "115 文件已复制，但无法确认新副本 ID；为避免改错同名文件，已停止后续改名"
            )

        # 3. 改名为目标名
        if target_name and target_name != source_name:
            try:
                rename_response = await client.fs_rename((new_id, target_name), async_=True)
                self._ensure_operation_succeeded(rename_response, "复制后改名")
            except Exception as exc:
                raise ValueError(
                    "115 文件已复制但改名失败；"
                    f"new_file_id={new_id}, target={target_name}"
                ) from exc

        return {"response": resp, "file_id": str(new_id)}

    async def _find_file_ids_in_dir(
        self,
        client: Any,
        parent_pid: str,
        name: str,
    ) -> set[str]:
        """分页返回父目录下所有同名文件 ID。"""
        matches: set[str] = set()
        offset = 0
        page_size = 100
        while True:
            try:
                response = await client.fs_files(
                    {
                        "cid": parent_pid,
                        "offset": offset,
                        "limit": page_size,
                        "show_dir": 1,
                    },
                    async_=True,
                )
            except Exception as exc:
                raise ValueError(
                    f"115 文件查询失败 parent_id={parent_pid} name={name}"
                ) from exc
            rows = response.get("data", []) if isinstance(response, dict) else []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                row_name = row.get("n") or row.get("name") or ""
                fid = row.get("fid")
                if row_name == name and fid:
                    matches.add(str(fid))
            if len(rows) < page_size:
                break
            offset += page_size
        return matches

    async def download(self, locator: StorageLocator, destination_dir: Path) -> Path:
        """下载 115 文件到本地临时目录。"""
        if not locator.file_id:
            raise ValueError("115 源文件缺少 file_id")

        from p115client.tool import download as download_tool

        filename = Path(locator.path).name or locator.file_id
        local_path = destination_dir / filename
        client, _ = await self._get_client()
        task_result = await download_tool.download_file(
            client,
            locator.file_id,
            path=str(local_path),
            async_=True,
        )
        if not task_result and task_result.error is not None:
            raise task_result.error
        return local_path


class ScraperService(ScraperConfigMixin, ScraperMetadataMixin, ScraperMediaMixin):
    """Service for orchestrating the complete scraping workflow.

    通过 Mixin 模式组织代码，保持核心刮削逻辑清晰：
    - ScraperConfigMixin: 配置检查和获取
    - ScraperMetadataMixin: NFO 生成和搜索结果处理
    - ScraperMediaMixin: 图片下载、字幕处理、Emby 冲突检测
    """

    def __init__(
        self,
        config_service: ConfigService,
        tmdb_service: TMDBService,
        parser_service: ParserService,
        nfo_service: NFOService,
        rename_service: RenameService,
        image_service: ImageService,
        subtitle_service: SubtitleService,
        emby_service: EmbyService,
    ) -> None:
        """Initialize scraper service with explicit dependencies.

        Args:
            config_service: Configuration service instance.
            tmdb_service: TMDB API service instance.
            parser_service: Filename parser service instance.
            nfo_service: NFO generation service instance.
            rename_service: File rename/move service instance.
            image_service: Image download service instance.
            subtitle_service: Subtitle handling service instance.
            emby_service: Emby integration service instance.
        """
        self.config_service = config_service
        self.tmdb_service = tmdb_service
        self.parser_service = parser_service
        self.nfo_service = nfo_service
        self.rename_service = rename_service
        self.image_service = image_service
        self.subtitle_service = subtitle_service
        self.emby_service = emby_service
        self.media_alias_service = MediaAliasService()

    async def _lookup_confirmed_alias(
        self,
        *,
        file_path: str,
        parsed_title: str | None,
    ) -> MediaAliasMatch | None:
        try:
            return await self.media_alias_service.lookup(
                file_path=file_path,
                parsed_title=parsed_title,
            )
        except Exception as exc:
            logger.warning("Unable to look up confirmed media alias: %s", exc)
            return None

    async def _remember_confirmed_aliases(
        self,
        *,
        file_path: str,
        parsed_title: str | None,
        tmdb_id: int,
        season: int,
        episode: int,
        series: TMDBSeries,
        source: str,
    ) -> None:
        try:
            await self.media_alias_service.remember_confirmed(
                file_path=file_path,
                parsed_title=parsed_title,
                tmdb_id=tmdb_id,
                season=season,
                episode=episode,
                canonical_titles=[series.name, series.original_name],
                source=source,
            )
        except Exception as exc:
            logger.warning("Unable to remember confirmed media aliases: %s", exc)

    async def _match_episode_by_multilingual_title(
        self,
        *,
        file_path: str,
        tmdb_id: int,
        series: TMDBSeries,
        season_hint: int | None,
    ) -> tuple[EpisodeMatch | None, list[TMDBSeason]]:
        """Fetch localized episode titles and return only a decisive match."""
        season_numbers = [
            season.season_number
            for season in series.seasons
            if season_hint is None or season.season_number == season_hint
        ]
        localized_seasons = []
        japanese_seasons = []
        for season_number in season_numbers:
            try:
                season = await self.tmdb_service.get_season_by_api(
                    tmdb_id,
                    season_number,
                )
                if season is not None:
                    localized_seasons.append(season)
            except (TMDBError, httpx.RequestError):
                raise
            except Exception as exc:
                logger.debug(
                    "Unable to fetch localized season %s for TMDB %s: %s",
                    season_number,
                    tmdb_id,
                    exc,
                )
            try:
                season_ja = await self.tmdb_service.get_season_by_api(
                    tmdb_id,
                    season_number,
                    "ja-JP",
                )
                if season_ja is not None:
                    japanese_seasons.append(season_ja)
            except (TMDBError, httpx.RequestError):
                raise
            except Exception as exc:
                logger.debug(
                    "Unable to fetch Japanese season %s for TMDB %s: %s",
                    season_number,
                    tmdb_id,
                    exc,
                )

        match = match_episode_from_titles(
            file_path=file_path,
            seasons_by_language=[localized_seasons, japanese_seasons],
            season_hint=season_hint,
        )
        return match, localized_seasons

    def _is_provider_source(self, locator: StorageLocator | None) -> bool:
        """判断是否为云端 provider 文件。"""
        return locator is not None and locator.provider != StorageProvider.LOCAL

    def _get_storage_provider(self, provider: StorageProvider):
        """按 provider 返回对应存储适配器。"""
        if provider == StorageProvider.P115:
            return _P115StorageProvider(self.config_service)
        raise ValueError(f"不支持的存储提供方: {provider}")

    async def _resolve_p115_output_locator(self, locator: StorageLocator) -> StorageLocator:
        """输出目录缺少 file_id 时，按路径解析出真实的 115 目录 id。"""
        from server.services.p115_service import P115Service
        svc = P115Service(self.config_service)
        config = await svc.config_service.get_115_config()
        if not config.is_logged_in:
            raise ValueError("请先登录 115 网盘")
        client = await svc._load_p115_client_with_config(config)
        normalized_path = svc._normalize_virtual_path(locator.path)
        directory_id = await svc._resolve_directory_id(
            client=client,
            path=normalized_path,
            file_id=None,
        )
        return StorageLocator(
            provider=StorageProvider.P115,
            path=locator.path,
            file_id=str(directory_id),
            is_dir=True,
        )

    def _auto_correct_season(
        self,
        requested_season: int,
        series: TMDBSeries,
    ) -> tuple[int, str | None]:
        """根据 TMDB 实际季数自动修正季号。

        规则：
        - 第 0 季始终保留，由后续核验确认特别篇是否存在
        - 计算实际可用正片季
        - 若请求的季号存在 → 保持不变
        - 若不存在且可用季 <= 2 → 自动选第一个可用季（通常为1），返回修正说明
        - 若不存在且可用季 > 2 → 保持原值（让后续核验拦截，交给用户选）

        返回 (修正后的季号, 修正说明或 None)。
        """
        if requested_season == 0:
            return requested_season, None
        real_seasons = sorted(
            s.season_number for s in series.seasons if s.season_number > 0
        )
        if not real_seasons:
            return requested_season, None
        if requested_season in real_seasons:
            return requested_season, None
        if len(real_seasons) <= 2:
            corrected = real_seasons[0]
            return corrected, f"TMDB 无第 {requested_season} 季，自动修正为第 {corrected} 季"
        # 多季：保持原值，让核验拦截交用户选择
        return requested_season, None

    def _build_rename_request(
        self,
        *,
        source_path: str,
        title: str,
        season: int,
        episode: int,
        output_dir: str | None,
        link_mode: OrganizeMode | None,
        year: int | None = None,
        advanced_settings: ManualJobAdvancedSettings | None = None,
    ) -> RenameRequest:
        """构建统一的整理请求。"""
        return RenameRequest(
            source_path=source_path,
            title=title,
            season=season,
            episode=episode,
            year=year,
            output_dir=output_dir,
            link_mode=link_mode,
            naming_template=_resolve_task_naming_template(advanced_settings),
        )

    async def _finalize_storage_output(
        self,
        *,
        file_locator: StorageLocator,
        output_locator: StorageLocator,
        metadata_locator: StorageLocator | None,
        link_mode: OrganizeMode | None,
        title: str,
        season: int,
        episode: int,
        source_path: str,
        year: int | None = None,
        advanced_settings: ManualJobAdvancedSettings | None = None,
    ) -> StorageLocator:
        """处理 115 网盘输出分支。"""
        if file_locator.provider != StorageProvider.P115:
            raise ValueError("仅支持 115 网盘文件走 provider 输出分支")

        mode = link_mode or OrganizeMode.MOVE
        rename_request = self._build_rename_request(
            source_path=source_path,
            title=title,
            season=season,
            episode=episode,
            output_dir=output_locator.path,
            link_mode=mode,
            year=year,
            advanced_settings=advanced_settings,
        )
        preview = self.rename_service.preview_rename(rename_request)
        dest_path = Path(preview.dest_path)
        output_base = Path(output_locator.path)

        if output_locator.provider == StorageProvider.P115:
            provider = self._get_storage_provider(StorageProvider.P115)

            # 输出目录必须有 file_id 才能创建子目录；缺失时按路径解析
            effective_output_locator = output_locator
            if not (output_locator.file_id or output_locator.parent_id):
                effective_output_locator = await self._resolve_p115_output_locator(output_locator)

            relative_dir = Path(preview.dest_folder).relative_to(output_base).as_posix()
            target_dir = await provider.ensure_directory(effective_output_locator, relative_dir)
            target_parent_id = target_dir.get("id")
            if not target_parent_id:
                raise ValueError("115 输出目录创建失败")

            if mode == OrganizeMode.COPY:
                operation_result = await provider.copy(
                    file_locator,
                    dest_path.name,
                    target_parent_id,
                )
            elif mode == OrganizeMode.MOVE:
                operation_result = await provider.rename(
                    file_locator,
                    dest_path.name,
                    target_parent_id,
                )
            else:
                raise ValueError(f"115 网盘暂不支持 {_get_mode_name(mode)} 输出")

            target_file_id = (
                operation_result.get("file_id")
                if isinstance(operation_result, dict)
                else None
            )
            if not target_file_id:
                raise ValueError("115 输出完成但缺少目标文件 ID")

            return StorageLocator(
                provider=StorageProvider.P115,
                path=str(dest_path).replace("\\", "/"),
                file_id=str(target_file_id),
                parent_id=str(target_parent_id),
                is_dir=False,
            )

        if output_locator.provider == StorageProvider.LOCAL:
            provider = self._get_storage_provider(StorageProvider.P115)
            with TemporaryDirectory(prefix="mhti-115-download-") as temp_dir:
                downloaded_path = await provider.download(file_locator, Path(temp_dir))
                local_request = self._build_rename_request(
                    source_path=str(downloaded_path),
                    title=title,
                    season=season,
                    episode=episode,
                    output_dir=output_locator.path,
                    # The downloaded source lives in a temporary directory;
                    # links would become invalid when that directory is removed.
                    link_mode=OrganizeMode.MOVE,
                    year=year,
                    advanced_settings=advanced_settings,
                )
                rename_result = await run_file_io(self.rename_service.execute_rename, local_request)
                if not rename_result.success:
                    raise ValueError(rename_result.error or "本地整理失败")
            return StorageLocator(
                provider=StorageProvider.LOCAL,
                path=output_locator.path,
                is_dir=True,
            )

        raise ValueError(f"不支持的输出提供方: {output_locator.provider}")

    async def _write_local_metadata_only(
        self,
        *,
        title: str,
        season: int,
        episode: int,
        year: int | None,
        metadata_dir: str | None,
        output_dir_for_preview: str | None,
        nfo_content: str,
        series,
        season_info,
        move_step: ScrapeLogStep,
        notify_log_update,
        link_mode: OrganizeMode | None,
        advanced_settings: ManualJobAdvancedSettings | None,
        dest_path_override: Path | None = None,
        require_metadata_dir: bool = True,
    ) -> tuple[str, Path, Path]:
        """在本地元数据目录写入 NFO/图片（视频已在 115，不落本地）。

        返回 (nfo_path, metadata_series_folder, metadata_season_folder)。
        仅在 metadata_dir 指向本地路径时执行；否则记录告警并返回空值。
        """
        if not metadata_dir and require_metadata_dir:
            move_step.logs.append(ScrapeLogEntry(
                message="未配置本地元数据目录，跳过 NFO/图片生成",
                level=LogLevel.WARNING,
            ))
            await notify_log_update()
            return "", Path(), Path()

        if dest_path_override is not None:
            dest_path = dest_path_override
            season_folder = dest_path.parent
            series_folder = season_folder.parent
        else:
            # 通过预览得到剧集/季文件夹结构（不实际移动文件）
            preview_request = self._build_rename_request(
                source_path=f"{title} S{season:02d}E{episode:02d}",
                title=title,
                season=season,
                episode=episode,
                year=year,
                output_dir=output_dir_for_preview,
                link_mode=link_mode,
                advanced_settings=advanced_settings,
            )
            preview = self.rename_service.preview_rename(preview_request)
            dest_path = Path(preview.dest_path)
            series_folder = Path(preview.dest_folder).parent
            season_folder = Path(preview.dest_folder)

        metadata_series_folder, metadata_season_folder = await self._resolve_metadata_folders(
            dest_file=dest_path,
            season_folder=season_folder,
            series_folder=series_folder,
            metadata_dir=metadata_dir,
        )
        # Local metadata is now prepared before the media operation, so a new
        # series/season directory may not exist yet.
        await run_file_io(
            metadata_season_folder.mkdir,
            parents=True,
            exist_ok=True,
        )

        # NFO
        nfo_config = await self._get_effective_nfo_config(advanced_settings)
        nfo_path_str = ""
        if nfo_config["nfo_enabled"]:
            nfo_path = metadata_season_folder / f"{dest_path.stem}.nfo"
            await run_file_io(write_metadata_text, nfo_path, nfo_content)
            nfo_path_str = str(nfo_path)
            move_step.logs.append(ScrapeLogEntry(message=f"NFO 文件已写入: {nfo_path}"))

            tvshow_nfo_path = metadata_series_folder / "tvshow.nfo"
            if not tvshow_nfo_path.exists():
                metadata_series_folder.mkdir(parents=True, exist_ok=True)
                tvshow_nfo_data = self.nfo_service.tvshow_from_tmdb(series)
                tvshow_nfo_content = self.nfo_service.generate_tvshow_nfo(tvshow_nfo_data)
                await run_file_io(write_metadata_text, tvshow_nfo_path, tvshow_nfo_content)
                move_step.logs.append(ScrapeLogEntry(message="tvshow.nfo 已生成"))

            season_nfo_path = metadata_season_folder / "season.nfo"
            if not season_nfo_path.exists():
                season_nfo_data = self._get_season_nfo_data(series, season)
                season_nfo_content = self.nfo_service.generate_season_nfo(season_nfo_data)
                await run_file_io(write_metadata_text, season_nfo_path, season_nfo_content)
                move_step.logs.append(ScrapeLogEntry(message="season.nfo 已生成"))
        else:
            move_step.logs.append(ScrapeLogEntry(message="NFO 生成已跳过（配置禁用）"))
        await notify_log_update()

        # 图片
        download_config = await self._get_effective_download_config(advanced_settings)
        overwrite_images = bool(download_config.get("overwrite_existing", False))
        if download_config["download_poster"] or download_config["download_fanart"]:
            await self._download_series_images(
                series,
                str(metadata_series_folder),
                download_poster=download_config["download_poster"],
                download_fanart=download_config["download_fanart"],
                overwrite_existing=overwrite_images,
            )
            move_step.logs.append(ScrapeLogEntry(message="剧集图片处理完成"))
        if download_config["download_thumb"]:
            await self._download_episode_image(
                season_info,
                season,
                episode,
                str(metadata_season_folder),
                dest_path.stem,
                overwrite_existing=overwrite_images,
            )
            move_step.logs.append(ScrapeLogEntry(message="集封面图处理完成"))
        await notify_log_update()

        return nfo_path_str, metadata_series_folder, metadata_season_folder

    async def _prepare_and_organize_local_output(
        self,
        *,
        rename_request: RenameRequest,
        source_display_path: str,
        output_dir_display: str | None,
        title: str,
        season: int,
        episode: int,
        year: int | None,
        metadata_dir: str | None,
        nfo_content: str,
        series: TMDBSeries,
        season_info: TMDBSeason | None,
        mode_name: str,
        move_step: ScrapeLogStep,
        notify_log_update: Callable[[], Awaitable[None]],
        result: ScrapeResult,
        advanced_settings: ManualJobAdvancedSettings | None,
    ) -> tuple[Path, Path, Path]:
        """Write all required metadata before publishing a local media file."""
        resolved_dest_path = await run_file_io(
            self.rename_service.resolve_destination_path,
            rename_request,
        )
        source_path = Path(rename_request.source_path)
        target_exists = resolved_dest_path.exists() or resolved_dest_path.is_symlink()
        if (
            target_exists
            and resolved_dest_path != source_path
            and rename_request.conflict_action != "overwrite"
        ):
            move_step.logs.append(
                ScrapeLogEntry(
                    message=f"目标文件已存在: {resolved_dest_path}",
                    level=LogLevel.WARNING,
                )
            )
            move_step.completed = False
            await notify_log_update()
            result.status = ScrapeStatus.FILE_CONFLICT
            result.message = f"目标文件已存在: {resolved_dest_path}"
            result.dest_path = str(resolved_dest_path)
            raise FileExistsError(resolved_dest_path)

        nfo_path, _, _ = await self._write_local_metadata_only(
            title=title,
            season=season,
            episode=episode,
            year=year,
            metadata_dir=metadata_dir,
            output_dir_for_preview=output_dir_display,
            nfo_content=nfo_content,
            series=series,
            season_info=season_info,
            move_step=move_step,
            notify_log_update=notify_log_update,
            link_mode=rename_request.link_mode,
            advanced_settings=advanced_settings,
            dest_path_override=resolved_dest_path,
            require_metadata_dir=False,
        )
        result.nfo_path = nfo_path or None

        return await self._organize_local_output(
            rename_request=rename_request,
            source_display_path=source_display_path,
            output_dir_display=output_dir_display,
            mode_name=mode_name,
            move_step=move_step,
            notify_log_update=notify_log_update,
            result=result,
            resolved_dest_path=resolved_dest_path,
        )

    def _resolve_move_input(
        self,
        *,
        file_path: str,
        file_locator: StorageLocator | None,
        output_dir: str | None,
        output_locator: StorageLocator | None,
        metadata_dir: str | None,
        metadata_locator: StorageLocator | None,
    ) -> tuple[str, str | None, str | None]:
        """统一解析视频/元数据输出目录。"""
        effective_output_dir = output_locator.path if output_locator else output_dir
        effective_metadata_dir = self._validate_metadata_directory(metadata_dir, metadata_locator)
        effective_source = file_locator.path if file_locator else file_path
        return effective_source, effective_output_dir, effective_metadata_dir

    @staticmethod
    def _validate_metadata_directory(
        metadata_dir: str | None, metadata_locator: StorageLocator | None,
    ) -> str | None:
        if metadata_locator and metadata_locator.provider != StorageProvider.LOCAL:
            raise PathSecurityError("元数据目录必须是允许的本地媒体目录")
        directory = metadata_locator.path if metadata_locator else metadata_dir
        return str(validate_media_path(directory)) if directory else None

    async def _organize_local_output(
        self,
        *,
        rename_request: RenameRequest,
        source_display_path: str,
        output_dir_display: str | None,
        mode_name: str,
        move_step: ScrapeLogStep,
        notify_log_update: Callable[[], Awaitable[None]],
        result: ScrapeResult,
        resolved_dest_path: Path | None = None,
    ) -> tuple[Path, Path, Path]:
        """沿用原本地整理链路。"""
        move_step.logs.append(ScrapeLogEntry(message=f"源文件: {source_display_path}"))
        move_step.logs.append(ScrapeLogEntry(message=f"目标目录: {output_dir_display or '原目录'}"))
        move_step.logs.append(ScrapeLogEntry(message=f"整理模式: {mode_name}"))
        await notify_log_update()

        rename_result = await run_file_io(
            self.rename_service.execute_rename,
            rename_request,
            False,
            resolved_dest_path,
        )
        if not rename_result.success:
            if rename_result.error and "already exists" in rename_result.error:
                move_step.logs.append(ScrapeLogEntry(message=f"目标文件已存在: {rename_result.dest_path}", level=LogLevel.WARNING))
                move_step.completed = False
                await notify_log_update()
                result.status = ScrapeStatus.FILE_CONFLICT
                result.message = f"目标文件已存在: {rename_result.dest_path}"
                result.dest_path = rename_result.dest_path
                raise FileExistsError(rename_result.dest_path)
            raise ValueError(rename_result.error or "整理失败")

        result.dest_path = rename_result.dest_path
        move_step.logs.append(ScrapeLogEntry(message=f"文件{mode_name}成功: {rename_result.dest_path}"))
        await self._safe_notify_log_update(notify_log_update, "文件输出成功日志")

        dest_file = Path(rename_result.dest_path)
        season_folder = dest_file.parent
        series_folder = season_folder.parent
        return dest_file, season_folder, series_folder

    @staticmethod
    async def _safe_notify_log_update(
        notify_log_update: Callable[[], Awaitable[None]],
        stage: str,
    ) -> None:
        """A log persistence outage after media publication must not trigger a retry."""
        try:
            await notify_log_update()
        except Exception:
            logger.exception("%s写入失败；媒体输出状态保持成功", stage)

    async def _resolve_metadata_folders(
        self,
        *,
        dest_file: Path,
        season_folder: Path,
        series_folder: Path,
        metadata_dir: str | None,
    ) -> tuple[Path, Path]:
        """确定本地元数据输出目录。"""
        if metadata_dir:
            metadata_base = validate_media_path(metadata_dir)
            metadata_series_folder = metadata_base / series_folder.name
            metadata_season_folder = metadata_series_folder / season_folder.name
            metadata_series_folder = validate_media_path(str(metadata_series_folder))
            metadata_season_folder = validate_media_path(str(metadata_season_folder))
            # metadata_season_folder is confined to an allowed media root.
            # codeql[py/path-injection]
            metadata_season_folder.mkdir(parents=True, exist_ok=True)
            return metadata_series_folder, metadata_season_folder

        return series_folder, season_folder

    async def _record_media_version(
        self,
        *,
        file_path: str,
        target_path: str | None,
        tmdb_id: int | None,
        season: int,
        episode: int,
        title: str | None,
    ) -> None:
        """Persist a successful local scrape as a media version without deleting anything."""
        if tmdb_id is None or not target_path:
            return
        try:
            from server.services.media_identity_service import MediaIdentityService
            await MediaIdentityService().record(
                file_path=file_path,
                target_path=target_path,
                tmdb_id=tmdb_id,
                season=season,
                episode=episode,
                title=title,
            )
        except Exception as exc:
            logger.warning("Unable to record media version: %s", exc)

    async def _check_emby_before_output(
        self,
        *,
        result: ScrapeResult,
        series: TMDBSeries,
        tmdb_id: int,
        season: int,
        episode: int,
        scrape_logs: list[ScrapeLogStep],
        notify_log_update: Callable[[], Awaitable[None]],
        skip: bool = False,
    ) -> bool:
        """Run the shared Emby guard and return whether output may continue."""
        emby_step = ScrapeLogStep(name="Emby 冲突检查", logs=[])
        scrape_logs.append(emby_step)
        if skip:
            emby_step.logs.append(ScrapeLogEntry(message="已按用户选择跳过 Emby 冲突检查"))
            await notify_log_update()
            return True

        try:
            conflict_result = await self._check_emby_conflict(
                series_name=series.name,
                tmdb_id=tmdb_id,
                season=season,
                episode=episode,
            )
        except Exception as exc:
            logger.warning("Emby 冲突检查异常: %s", exc)
            from server.models.emby import ConflictCheckResult

            conflict_result = ConflictCheckResult(
                conflict_type=ConflictType.CHECK_FAILED,
                message=f"Emby 冲突检查失败: {exc}",
            )

        if conflict_result.conflict_type == ConflictType.CHECK_FAILED:
            emby_step.logs.append(
                ScrapeLogEntry(
                    message=conflict_result.message or "Emby 冲突检查失败",
                    level=LogLevel.ERROR,
                )
            )
            emby_step.completed = False
            result.status = ScrapeStatus.API_FAILED
            result.message = conflict_result.message or "Emby 冲突检查失败"
            result.emby_conflict = conflict_result
            result.scrape_logs = scrape_logs
            await notify_log_update()
            return False

        if conflict_result.conflict_type == ConflictType.EPISODE_EXISTS:
            emby_step.logs.append(
                ScrapeLogEntry(
                    message=conflict_result.message or "Emby 中已存在该集",
                    level=LogLevel.WARNING,
                )
            )
            emby_step.completed = False
            result.status = ScrapeStatus.EMBY_CONFLICT
            result.message = conflict_result.message
            result.emby_conflict = conflict_result
            result.scrape_logs = scrape_logs
            await notify_log_update()
            return False

        if conflict_result.conflict_type == ConflictType.SERIES_EXISTS:
            emby_step.logs.append(
                ScrapeLogEntry(
                    message=conflict_result.message or "Emby 中已存在该剧集",
                    level=LogLevel.SUCCESS,
                )
            )
        else:
            emby_step.logs.append(ScrapeLogEntry(message="无冲突"))
        await notify_log_update()
        return True

    async def _complete_scrape_output(
        self,
        *,
        result: ScrapeResult,
        file_path: str,
        tmdb_id: int,
        series: TMDBSeries,
        season_info: TMDBSeason | None,
        season: int,
        episode: int,
        scrape_logs: list[ScrapeLogStep],
        notify_log_update: Callable[[], Awaitable[None]],
        remember_manual_alias: bool,
        parsed_title: str | None,
    ) -> ScrapeResult:
        """Finalize every successful output path in one place."""
        if season_info and season_info.episodes:
            result.episode_info = next(
                (item for item in season_info.episodes if item.episode_number == episode),
                None,
            )

        finalization_warnings: list[str] = []
        try:
            await self._record_media_version(
                file_path=file_path,
                target_path=result.dest_path,
                tmdb_id=tmdb_id,
                season=season,
                episode=episode,
                title=series.name,
            )
        except Exception:
            logger.exception("媒体已输出，但媒体版本记录写入失败: %s", result.dest_path)
            finalization_warnings.append("媒体版本记录写入失败")
        if remember_manual_alias:
            try:
                await self._remember_confirmed_aliases(
                    file_path=file_path,
                    parsed_title=parsed_title,
                    tmdb_id=tmdb_id,
                    season=season,
                    episode=episode,
                    series=series,
                    source="manual",
                )
            except Exception:
                # file_path is converted to a bounded single-line value.
                # codeql[py/log-injection]
                logger.exception(
                    "媒体已输出，但手动别名记录写入失败: %s",
                    safe_log_value(file_path),
                )
                finalization_warnings.append("手动别名记录写入失败")

        if finalization_warnings:
            scrape_logs[-1].logs.append(
                ScrapeLogEntry(
                    message="；".join(finalization_warnings),
                    level=LogLevel.WARNING,
                )
            )

        result.status = ScrapeStatus.SUCCESS
        result.message = "刮削完成"
        result.scrape_logs = scrape_logs
        await self._safe_notify_log_update(notify_log_update, "任务完成日志")
        return result

    async def _execute_scrape_output(
        self,
        *,
        request: ScrapeRequest | ScrapeByIdRequest,
        result: ScrapeResult,
        series: TMDBSeries,
        season_info: TMDBSeason | None,
        season: int,
        episode: int,
        scrape_logs: list[ScrapeLogStep],
        notify_log_update: Callable[[], Awaitable[None]],
        file_action: str | None = None,
        remember_manual_alias: bool = False,
        parsed_title: str | None = None,
    ) -> ScrapeResult:
        """Generate metadata and organize media for auto and manual matches."""
        file_path = request.file_path
        tmdb_id = result.selected_id
        if tmdb_id is None:
            raise ValueError("刮削输出缺少 TMDB ID")

        task_settings = request.advanced_settings
        effective_file_action, should_process_subtitles = (
            _resolve_task_output_preferences(task_settings, file_action)
        )

        result.parsed_season = season
        result.parsed_episode = episode

        nfo_step = ScrapeLogStep(name="生成 NFO", logs=[])
        scrape_logs.append(nfo_step)
        try:
            nfo_content = self._generate_episode_nfo(series, season, episode, season_info)
            nfo_step.logs.append(ScrapeLogEntry(message="NFO 内容生成成功"))
            await notify_log_update()
        except Exception as exc:
            nfo_step.logs.append(
                ScrapeLogEntry(message=f"NFO 生成失败: {exc}", level=LogLevel.ERROR)
            )
            nfo_step.completed = False
            await notify_log_update()
            result.status = ScrapeStatus.NFO_FAILED
            result.message = f"NFO 生成失败: {exc}"
            result.scrape_logs = scrape_logs
            return result

        mode_name = _get_mode_name(request.link_mode)
        move_step = ScrapeLogStep(name=f"{mode_name}文件", logs=[])
        scrape_logs.append(move_step)
        try:
            year = series.first_air_date.year if series.first_air_date else None
            source_display_path, effective_output_dir, effective_metadata_dir = (
                self._resolve_move_input(
                    file_path=file_path,
                    file_locator=request.file_locator,
                    output_dir=request.output_dir,
                    output_locator=request.output_locator,
                    metadata_dir=request.metadata_dir,
                    metadata_locator=request.metadata_locator,
                )
            )
            if self._is_provider_source(request.file_locator) and request.output_locator:
                move_step.logs.append(ScrapeLogEntry(message=f"源文件: {source_display_path}"))
                move_step.logs.append(
                    ScrapeLogEntry(message=f"目标目录: {request.output_locator.path}")
                )
                move_step.logs.append(ScrapeLogEntry(message=f"整理模式: {mode_name}"))
                await notify_log_update()

                if request.output_locator.provider == StorageProvider.P115:
                    move_step.logs.append(
                        ScrapeLogEntry(message="先生成本地元数据，再输出 115 网盘视频")
                    )
                    await notify_log_update()
                    nfo_path_str, _, _ = await self._write_local_metadata_only(
                        title=series.name,
                        season=season,
                        episode=episode,
                        year=year,
                        metadata_dir=effective_metadata_dir,
                        output_dir_for_preview=effective_output_dir,
                        nfo_content=nfo_content,
                        series=series,
                        season_info=season_info,
                        move_step=move_step,
                        notify_log_update=notify_log_update,
                        link_mode=request.link_mode,
                        advanced_settings=task_settings,
                    )
                    result.nfo_path = nfo_path_str or None
                    dest_locator = await self._finalize_storage_output(
                        file_locator=request.file_locator,
                        output_locator=request.output_locator,
                        metadata_locator=request.metadata_locator,
                        link_mode=request.link_mode,
                        title=series.name,
                        season=season,
                        episode=episode,
                        source_path=source_display_path,
                        year=year,
                        advanced_settings=task_settings,
                    )
                    result.dest_path = dest_locator.path
                    move_step.logs.append(
                        ScrapeLogEntry(message=f"文件{mode_name}成功: {dest_locator.path}")
                    )
                    await self._safe_notify_log_update(
                        notify_log_update,
                        "115 文件输出成功日志",
                    )
                    return await self._complete_scrape_output(
                        result=result,
                        file_path=file_path,
                        tmdb_id=tmdb_id,
                        series=series,
                        season_info=season_info,
                        season=season,
                        episode=episode,
                        scrape_logs=scrape_logs,
                        notify_log_update=notify_log_update,
                        remember_manual_alias=remember_manual_alias,
                        parsed_title=parsed_title,
                    )

                if request.output_locator.provider == StorageProvider.LOCAL:
                    provider = self._get_storage_provider(request.file_locator.provider)
                    with TemporaryDirectory(prefix="mhti-115-download-") as temp_dir:
                        downloaded_path = await provider.download(
                            request.file_locator, Path(temp_dir)
                        )
                        local_source_path = str(downloaded_path)
                        should_process_subtitles = False
                        rename_request = self._build_rename_request(
                            source_path=local_source_path,
                            title=series.name,
                            season=season,
                            episode=episode,
                            year=year,
                            output_dir=effective_output_dir,
                            # Provider downloads are temporary. Publishing by
                            # move keeps the final file valid after cleanup.
                            link_mode=OrganizeMode.MOVE,
                            advanced_settings=task_settings,
                        )
                        rename_request.conflict_action = effective_file_action
                        dest_file, _, _ = (
                            await self._prepare_and_organize_local_output(
                                rename_request=rename_request,
                                source_display_path=source_display_path,
                                output_dir_display=effective_output_dir,
                                title=series.name,
                                season=season,
                                episode=episode,
                                year=year,
                                metadata_dir=effective_metadata_dir,
                                nfo_content=nfo_content,
                                series=series,
                                season_info=season_info,
                                mode_name=mode_name,
                                move_step=move_step,
                                notify_log_update=notify_log_update,
                                result=result,
                                advanced_settings=task_settings,
                            )
                        )
                else:
                    raise ValueError(
                        f"不支持的输出提供方: {request.output_locator.provider}"
                    )
            else:
                local_source_path = source_display_path
                rename_request = self._build_rename_request(
                    source_path=local_source_path,
                    title=series.name,
                    season=season,
                    episode=episode,
                    year=year,
                    output_dir=effective_output_dir,
                    link_mode=request.link_mode,
                    advanced_settings=task_settings,
                )
                rename_request.conflict_action = effective_file_action
                dest_file, _, _ = (
                    await self._prepare_and_organize_local_output(
                        rename_request=rename_request,
                        source_display_path=source_display_path,
                        output_dir_display=effective_output_dir,
                        title=series.name,
                        season=season,
                        episode=episode,
                        year=year,
                        metadata_dir=effective_metadata_dir,
                        nfo_content=nfo_content,
                        series=series,
                        season_info=season_info,
                        mode_name=mode_name,
                        move_step=move_step,
                        notify_log_update=notify_log_update,
                        result=result,
                        advanced_settings=task_settings,
                    )
                )

            if should_process_subtitles:
                try:
                    await run_file_io(
                        self._process_subtitles,
                        local_source_path,
                        str(dest_file),
                        request.link_mode,
                    )
                except Exception as exc:
                    logger.exception("媒体已输出，但字幕处理失败: %s", dest_file)
                    move_step.logs.append(
                        ScrapeLogEntry(
                            message=f"字幕处理失败，视频仍保持成功: {exc}",
                            level=LogLevel.WARNING,
                        )
                    )
        except FileExistsError:
            result.scrape_logs = scrape_logs
            return result
        except Exception as exc:
            move_step.logs.append(
                ScrapeLogEntry(
                    message=f"文件{mode_name}失败: {exc}",
                    level=LogLevel.ERROR,
                )
            )
            move_step.completed = False
            await notify_log_update()
            result.status = ScrapeStatus.MOVE_FAILED
            result.message = f"文件{mode_name}失败: {exc}"
            result.scrape_logs = scrape_logs
            return result

        return await self._complete_scrape_output(
            result=result,
            file_path=file_path,
            tmdb_id=tmdb_id,
            series=series,
            season_info=season_info,
            season=season,
            episode=episode,
            scrape_logs=scrape_logs,
            notify_log_update=notify_log_update,
            remember_manual_alias=remember_manual_alias,
            parsed_title=parsed_title,
        )

    async def preview(self, file_path: str) -> ScrapePreview:
        """Preview scrape operation without executing.

        Args:
            file_path: Path to the video file.

        Returns:
            ScrapePreview with parsed info and search results.
        """
        path = Path(file_path)

        # Parse filename
        parsed = self.parser_service.parse(path.name, file_path)

        preview = ScrapePreview(
            file_path=file_path,
            parsed_title=parsed.series_name,
            parsed_season=parsed.season,
            parsed_episode=parsed.episode,
        )

        # Search TMDB if we have a title
        if parsed.series_name:
            try:
                merged_results: list[TMDBSearchResult] = []
                for search_title in build_search_title_variants(parsed.series_name):
                    search_response = await self.tmdb_service.search_series_by_api(
                        search_title
                    )
                    merged_results = merge_search_results(
                        merged_results,
                        search_response.results,
                    )
                    if any(item.adult for item in search_response.results):
                        break
                preview.search_results = merged_results
            except (httpx.TimeoutException, httpx.RequestError):
                pass

        return preview

    async def scrape_file(
        self,
        request: ScrapeRequest,
        on_log_update: LogUpdateCallback | None = None,
    ) -> ScrapeResult:
        """Execute complete scraping workflow for a single file.

        Workflow:
        1. Parse filename to extract series name, season, episode
        2. Search TMDB using API
        3. Auto-select best match (or return candidates)
        4. Get details via API
        5. Generate NFO
        6. Move file to organized location
        7. Download images
        8. Process subtitles

        Args:
            request: Scrape request with file path and options.
            on_log_update: Optional callback for real-time log updates.

        Returns:
            ScrapeResult with operation status and details.
        """
        try:
            self._validate_metadata_directory(request.metadata_dir, request.metadata_locator)
        except PathSecurityError as exc:
            return ScrapeResult(file_path=request.file_path, status=ScrapeStatus.MOVE_FAILED, message=str(exc))
        file_path = request.file_path
        path = Path(file_path)
        scrape_logs: list[ScrapeLogStep] = []

        async def notify_log_update():
            """通知日志更新。"""
            if on_log_update:
                await on_log_update(scrape_logs)

        # Check file exists
        if not self._is_provider_source(request.file_locator):
            try:
                path = validate_media_path(
                    file_path,
                    must_exist=True,
                    require_file=True,
                )
            except PathSecurityError as exc:
                return ScrapeResult(
                    file_path=file_path,
                    status=ScrapeStatus.MOVE_FAILED,
                    message=str(exc),
                )

        # Step 1: Parse filename
        parse_step = ScrapeLogStep(name="解析文件名", logs=[])
        parse_step.logs.append(ScrapeLogEntry(message=f"视频文件路径: {file_path}"))
        parsed = self.parser_service.parse(path.name, file_path)

        result = ScrapeResult(
            file_path=file_path,
            status=ScrapeStatus.SUCCESS,
            parsed_title=parsed.series_name,
            parsed_season=parsed.season,
            parsed_episode=parsed.episode,
        )

        if not parsed.series_name:
            parse_step.logs.append(ScrapeLogEntry(message="无法从文件名解析出剧集名称", level=LogLevel.ERROR))
            parse_step.completed = False
            scrape_logs.append(parse_step)
            await notify_log_update()
            result.status = ScrapeStatus.NO_MATCH
            result.message = "无法从文件名解析出剧集名称"
            result.scrape_logs = scrape_logs
            return result

        parse_step.logs.append(ScrapeLogEntry(message=f"解析结果: {parsed.series_name} S{parsed.season if parsed.season is not None else '?'}E{parsed.episode if parsed.episode is not None else '?'}"))
        scrape_logs.append(parse_step)
        await notify_log_update()

        # Step 2: Resolve a confirmed local alias, then search TMDB with a
        # bounded query ladder. Search expansion is intentionally separate
        # from permission to auto-select a result.
        search_step = ScrapeLogStep(name="搜索 TMDB", logs=[])
        scrape_logs.append(search_step)
        await notify_log_update()

        alias_match = await self._lookup_confirmed_alias(
            file_path=file_path,
            parsed_title=parsed.series_name,
        )
        all_results: list[TMDBSearchResult] = []
        adult_results: list[TMDBSearchResult] = []
        attempted_titles: list[str] = []
        candidate_sources: dict[int, set[str]] = {}

        if alias_match is not None:
            result.selected_id = alias_match.tmdb_id
            if alias_match.season is not None:
                parsed.season = alias_match.season
                result.parsed_season = alias_match.season
            if alias_match.episode is not None:
                parsed.episode = alias_match.episode
                result.parsed_episode = alias_match.episode
            search_step.logs.append(ScrapeLogEntry(
                message=(
                    f"命中已确认本地别名: {alias_match.alias} "
                    f"→ TMDB {alias_match.tmdb_id}"
                )
            ))
            await notify_log_update()
        else:
            deterministic_titles = build_search_title_variants(parsed.series_name)
            for index, search_title in enumerate(deterministic_titles):
                attempted_titles.append(search_title)
                search_step.logs.append(ScrapeLogEntry(message=f"搜索关键词: {search_title}"))
                await notify_log_update()
                try:
                    search_response = await self.tmdb_service.search_series_by_api(search_title)
                except TMDBError as exc:
                    search_step.logs.append(ScrapeLogEntry(message=str(exc), level=LogLevel.ERROR))
                    search_step.completed = False
                    await notify_log_update()
                    result.status = ScrapeStatus.SEARCH_FAILED
                    result.message = str(exc)
                    result.scrape_logs = scrape_logs
                    return result
                except httpx.TimeoutException:
                    search_step.logs.append(ScrapeLogEntry(
                        message="TMDB 搜索超时",
                        level=LogLevel.ERROR,
                    ))
                    search_step.completed = False
                    await notify_log_update()
                    result.status = ScrapeStatus.SEARCH_FAILED
                    result.message = "TMDB 搜索超时，请稍后重试或检查网络"
                    result.scrape_logs = scrape_logs
                    return result
                except httpx.RequestError as e:
                    search_step.logs.append(ScrapeLogEntry(
                        message=f"TMDB 搜索失败: {str(e)}",
                        level=LogLevel.ERROR,
                    ))
                    search_step.completed = False
                    await notify_log_update()
                    result.status = ScrapeStatus.SEARCH_FAILED
                    result.message = f"TMDB 搜索失败: {str(e)}"
                    result.scrape_logs = scrape_logs
                    return result

                all_results = merge_search_results(all_results, search_response.results)
                query_adult = [item for item in search_response.results if item.adult]
                for item in query_adult:
                    candidate_sources.setdefault(item.id, set()).add(
                        f"deterministic:{index}"
                    )
                adult_results = merge_search_results(adult_results, query_adult)
                search_step.logs.append(ScrapeLogEntry(
                    message=f"该关键词找到 {len(query_adult)} 个成人候选"
                ))
                await notify_log_update()
                if query_adult:
                    break

            result.search_results = adult_results

        # Step 2.5: Ask the configured AI to refine the title, season/episode,
        # and select a TMDB candidate. This keeps the final metadata source as
        # TMDB while allowing difficult filenames (including .strm) to be
        # interpreted by the configured OpenAI-compatible provider.
        ai_selected: TMDBSearchResult | None = None
        ai_provider = AIProviderService(self.config_service)
        ai_config = await ai_provider.get_config()
        force_ai = ai_config.enabled and ai_config.usage_mode == AIUsageMode.FORCE_USE
        ai_required_but_failed = False
        if ai_config.enabled and _should_use_ai(
            ai_config.usage_mode,
            has_confirmed_alias=alias_match is not None,
            has_adult_candidates=bool(adult_results),
        ):
            ai_step = ScrapeLogStep(
                name="AI 强制识别" if force_ai else "AI 辅助识别",
                logs=[],
            )
            scrape_logs.append(ai_step)
            candidates = [
                AICandidate(
                    id=item.id,
                    title=item.name,
                    original_title=item.original_name,
                    year=item.first_air_date.year if item.first_air_date else None,
                    overview=item.overview,
                )
                for item in all_results[:10]
            ]
            evidence = {
                "filename": path.name,
                "parsed_title": parsed.series_name,
                "parsed_season": parsed.season,
                "parsed_episode": parsed.episode,
                "parser_confidence": parsed.confidence,
                "suffix": path.suffix.lower(),
                "release_year_month": extract_release_year_month(file_path),
            }
            try:
                ai_result = await ai_provider.recognize(
                    file_path=file_path,
                    evidence=evidence,
                    candidates=candidates,
                )
                can_auto_apply = _can_auto_apply_ai_result(ai_result)
                if can_auto_apply:
                    if ai_result.title:
                        parsed.series_name = ai_result.title
                        result.parsed_title = ai_result.title
                    if ai_result.season is not None:
                        parsed.season = ai_result.season
                        result.parsed_season = ai_result.season
                    if ai_result.episode is not None:
                        parsed.episode = ai_result.episode
                        result.parsed_episode = ai_result.episode

                    selected_id = str(ai_result.selected_candidate_id) if ai_result.selected_candidate_id is not None else None
                    selected_candidate = next(
                        (item for item in all_results if str(item.id) == selected_id and item.adult),
                        None,
                    )
                    if selected_candidate is not None:
                        ai_selected = selected_candidate
                        adult_results = [ai_selected]
                        result.search_results = adult_results
                        candidate_sources.setdefault(ai_selected.id, set()).add(
                            "ai_confirmed"
                        )
                        ai_step.logs.append(ScrapeLogEntry(message=f"AI 高置信度选择 TMDB 候选: {ai_selected.name} (ID {ai_selected.id})"))
                else:
                    ai_step.logs.append(ScrapeLogEntry(
                        message=(
                            "AI 结果置信度不足：保留原始标题和季集，"
                            "仅使用建议标题扩展候选，不允许自动选择"
                        ),
                        level=LogLevel.WARNING,
                    ))

                # Search aliases are discovery-only and therefore useful even
                # when the model cannot safely choose an ID or episode. Results
                # discovered only from an unconfirmed alias remain manual.
                search_titles = [ai_result.title, *ai_result.search_titles]
                search_titles = list(dict.fromkeys(
                    title.strip()
                    for title in search_titles
                    if title and title.strip()
                ))
                if not adult_results:
                    for search_title in search_titles:
                        if search_title in attempted_titles:
                            continue
                        attempted_titles.append(search_title)
                        retry_response = await self.tmdb_service.search_series_by_api(
                            search_title
                        )
                        all_results = merge_search_results(
                            all_results,
                            retry_response.results,
                        )
                        retry_adult_results = [
                            item for item in retry_response.results if item.adult
                        ]
                        source = (
                            "ai_confirmed" if can_auto_apply else "ai_suggestion"
                        )
                        for item in retry_adult_results:
                            candidate_sources.setdefault(item.id, set()).add(source)
                        adult_results = merge_search_results(
                            adult_results,
                            retry_adult_results,
                        )
                        result.search_results = adult_results
                        ai_step.logs.append(ScrapeLogEntry(
                            message=(
                                f"AI 检索标题“{search_title}”得到 "
                                f"{len(retry_adult_results)} 个成人候选"
                            )
                        ))
                        if retry_adult_results:
                            break

                if not ai_selected and not adult_results:
                    ai_step.logs.append(ScrapeLogEntry(message=ai_result.reason or "AI 未给出可用候选", level=LogLevel.WARNING))
                await notify_log_update()
            except AIProviderError as exc:
                ai_required_but_failed = force_ai
                fallback = "等待人工确认" if force_ai else "回退到常规刮削"
                ai_step.logs.append(ScrapeLogEntry(message=f"AI 识别失败，{fallback}: {exc}", level=LogLevel.WARNING))
                ai_step.completed = False
                await notify_log_update()
            except (TMDBError, httpx.RequestError) as exc:
                ai_step.logs.append(ScrapeLogEntry(
                    message=f"AI 建议标题的 TMDB 搜索失败: {exc}",
                    level=LogLevel.ERROR,
                ))
                ai_step.completed = False
                await notify_log_update()
                result.status = ScrapeStatus.SEARCH_FAILED
                result.message = f"AI 建议标题的 TMDB 搜索失败: {exc}"
                result.scrape_logs = scrape_logs
                return result

        if alias_match is None and not adult_results:
            search_step.logs.append(ScrapeLogEntry(message="未找到匹配的成人剧集", level=LogLevel.WARNING))
            search_step.completed = False
            await notify_log_update()
            result.status = ScrapeStatus.NO_MATCH
            result.message = f"未找到匹配的成人剧集: {parsed.series_name}"
            result.scrape_logs = scrape_logs
            return result

        # Step 3: Select match
        result.search_results = adult_results

        if alias_match is not None:
            result.selected_id = alias_match.tmdb_id
        elif request.auto_select and ai_selected is not None:
            selected = ai_selected
            result.selected_id = selected.id
        elif request.auto_select and force_ai:
            search_step.logs.append(ScrapeLogEntry(message="获取各剧集详情..."))
            await notify_log_update()
            result.search_results = await self._enrich_search_results(adult_results)
            result.status = ScrapeStatus.NEED_SELECTION
            if ai_required_but_failed:
                result.message = "AI 强制识别失败，已禁止常规自动选择，请手动确认"
            else:
                result.message = "AI 强制识别未给出高置信度候选，请手动确认"
            result.scrape_logs = scrape_logs
            return result
        elif request.auto_select:
            ranked_match = select_series_candidate(
                adult_results,
                attempted_titles,
            )
            trusted_match = (
                ranked_match is not None
                and candidate_sources.get(ranked_match.candidate.id, set())
                != {"ai_suggestion"}
            )
            if trusted_match:
                selected_match = ranked_match.candidate
                result.selected_id = selected_match.id
                score_text = (
                    f"score={ranked_match.score:.2f}, "
                    f"margin={ranked_match.margin:.2f}"
                )
                search_step.logs.append(ScrapeLogEntry(
                    message=(
                        f"标题评分自动选择: {selected_match.name} "
                        f"(TMDB {selected_match.id}, {score_text})"
                    )
                ))
                await notify_log_update()
            else:
                search_step.logs.append(ScrapeLogEntry(message="获取各剧集详情..."))
                await notify_log_update()
                enriched_results = await self._enrich_search_results(adult_results)
                result.search_results = enriched_results
                result.status = ScrapeStatus.NEED_SELECTION
                if ranked_match is not None and not trusted_match:
                    result.message = "AI 建议标题找到了候选，但置信度不足，请手动确认"
                else:
                    result.message = f"找到 {len(adult_results)} 个候选，请手动选择"
                result.scrape_logs = scrape_logs
                return result
        elif len(adult_results) > 0:
            # 多个结果时需要用户选择，先获取每个结果的详情
            search_step.logs.append(ScrapeLogEntry(message="获取各剧集详情..."))
            await notify_log_update()
            enriched_results = await self._enrich_search_results(adult_results)
            result.search_results = enriched_results
            result.status = ScrapeStatus.NEED_SELECTION
            result.message = "请手动选择匹配的剧集"
            result.scrape_logs = scrape_logs
            return result

        # Step 4: Get details via API
        detail_step = ScrapeLogStep(name="获取详情", logs=[])
        detail_step.logs.append(ScrapeLogEntry(message=f"获取剧集详情: TMDB ID {result.selected_id}"))
        scrape_logs.append(detail_step)
        await notify_log_update()

        try:
            series = await self.tmdb_service.get_series_by_api(result.selected_id)
            if series is None:
                detail_step.logs.append(ScrapeLogEntry(message="无法获取剧集详情", level=LogLevel.ERROR))
                detail_step.completed = False
                await notify_log_update()
                result.status = ScrapeStatus.API_FAILED
                result.message = f"无法获取剧集详情: ID {result.selected_id}"
                result.scrape_logs = scrape_logs
                return result
            result.series_info = series
            detail_step.logs.append(ScrapeLogEntry(message=f"剧集名称: {series.name}"))
            await notify_log_update()
        except (TMDBError, ValueError) as e:
            detail_step.logs.append(ScrapeLogEntry(message=str(e), level=LogLevel.ERROR))
            detail_step.completed = False
            await notify_log_update()
            result.status = ScrapeStatus.API_FAILED
            result.message = str(e)
            result.scrape_logs = scrape_logs
            return result
        except httpx.TimeoutException:
            detail_step.logs.append(ScrapeLogEntry(message="TMDB API 请求超时", level=LogLevel.ERROR))
            detail_step.completed = False
            await notify_log_update()
            result.status = ScrapeStatus.API_FAILED
            result.message = "TMDB API 请求超时"
            result.scrape_logs = scrape_logs
            return result
        except httpx.RequestError as e:
            detail_step.logs.append(ScrapeLogEntry(message=f"TMDB API 请求失败: {str(e)}", level=LogLevel.ERROR))
            detail_step.completed = False
            await notify_log_update()
            result.status = ScrapeStatus.API_FAILED
            result.message = f"TMDB API 请求失败: {str(e)}"
            result.scrape_logs = scrape_logs
            return result

        # Step 4.5: Check if episode is missing
        if parsed.episode is None:
            try:
                episode_match, localized_seasons = (
                    await self._match_episode_by_multilingual_title(
                        file_path=file_path,
                        tmdb_id=result.selected_id,
                        series=series,
                        season_hint=parsed.season,
                    )
                )
            except (TMDBError, httpx.RequestError) as exc:
                detail_step.logs.append(ScrapeLogEntry(message=str(exc), level=LogLevel.ERROR))
                detail_step.completed = False
                await notify_log_update()
                result.status = ScrapeStatus.API_FAILED
                result.message = str(exc)
                result.scrape_logs = scrape_logs
                return result
            if episode_match is not None:
                parsed.season = episode_match.season
                parsed.episode = episode_match.episode
                result.parsed_season = episode_match.season
                result.parsed_episode = episode_match.episode
                detail_step.logs.append(ScrapeLogEntry(
                    message=(
                        f"跨语言集标题匹配: S{episode_match.season:02d}"
                        f"E{episode_match.episode:02d} "
                        f"“{episode_match.matched_title}” "
                        f"(score={episode_match.score:.2f}, "
                        f"margin={episode_match.margin:.2f})"
                    )
                ))
                await notify_log_update()
            # 如果剧集只有1集，自动选择
            total_episodes = series.number_of_episodes or 0
            if parsed.episode is None and total_episodes == 1:
                parsed.episode = 1
                logger.info("剧集只有1集，自动选择 E01")
            elif parsed.episode is None:
                # 匹配分数或领先幅度不足，保留人工选择。
                result.series_info = series
                for season_detail in localized_seasons:
                    for i, existing_season in enumerate(series.seasons):
                        if existing_season.season_number == season_detail.season_number:
                            series.seasons[i] = season_detail
                            break
                result.series_info = series

                result.status = ScrapeStatus.NEED_SEASON_EPISODE
                result.message = (
                    f"剧集共 {total_episodes} 集，集标题匹配不足以自动采用，请手动选择"
                )
                result.scrape_logs = scrape_logs
                return result

        # Determine season and episode
        season_num = parsed.season if parsed.season is not None else 1
        episode_num = parsed.episode if parsed.episode is not None else 1

        # 根据 TMDB 实际季数自动修正季号（单季/双季自动回退，多季交用户选）
        season_num, season_correction = self._auto_correct_season(season_num, series)

        # 记录程序选择的季/集
        select_step = ScrapeLogStep(name="确定季/集", logs=[])
        scrape_logs.append(select_step)
        if parsed.season is not None and parsed.episode is not None and not season_correction:
            select_step.logs.append(ScrapeLogEntry(message=f"从文件名解析: S{season_num:02d}E{episode_num:02d}"))
        else:
            msgs = []
            if parsed.season is None:
                msgs.append("季号默认为 1")
            if parsed.episode is None:
                msgs.append("集号默认为 1")
            if season_correction:
                msgs.append(season_correction)
            select_step.logs.append(ScrapeLogEntry(message=f"程序自动选择: S{season_num:02d}E{episode_num:02d} ({', '.join(msgs)})"))
        await notify_log_update()

        # Step 5: Get season details (for episode info)
        season_info = None
        try:
            season_info = await self.tmdb_service.get_season_by_api(
                result.selected_id, season_num
            )
            logger.info(f"获取季度详情: Season {season_num}, 共 {len(season_info.episodes) if season_info and season_info.episodes else 0} 集")
        except Exception as e:
            detail_step.logs.append(ScrapeLogEntry(message=f"获取季度详情失败: {e}", level=LogLevel.ERROR))
            detail_step.completed = False
            await notify_log_update()
            result.status = ScrapeStatus.API_FAILED
            result.message = f"获取季度详情失败: {e}"
            result.scrape_logs = scrape_logs
            return result

        # Step 5.2: 核验 TMDB 中是否存在该季和该集
        # 若不存在则暂停为 pending_action，避免文件被错误重命名/移动
        verify_step = ScrapeLogStep(name="核验季/集", logs=[])
        scrape_logs.append(verify_step)

        season_exists = any(s.season_number == season_num for s in series.seasons)
        episode_exists = (
            season_info is not None
            and bool(season_info.episodes)
            and any(e.episode_number == episode_num for e in season_info.episodes)
        )

        if not season_exists or not episode_exists:
            reasons = []
            if not season_exists:
                reasons.append(f"TMDB 中不存在第 {season_num} 季")
            if season_exists and not episode_exists:
                reasons.append(f"TMDB 第 {season_num} 季中不存在第 {episode_num} 集")
            reason_text = "，".join(reasons)
            verify_step.logs.append(ScrapeLogEntry(
                message=f"核验失败: {reason_text}", level=LogLevel.WARNING,
            ))
            verify_step.completed = False
            await notify_log_update()

            # 核验失败：标记待处理，让用户在记录页手动选择正确的季/集
            result.status = ScrapeStatus.NEED_SEASON_EPISODE
            result.message = f"需要确认季/集: {reason_text}"
            result.series_info = series
            result.parsed_season = season_num
            result.parsed_episode = episode_num
            result.scrape_logs = scrape_logs
            return result

        verify_step.logs.append(ScrapeLogEntry(
            message=f"核验通过: S{season_num:02d}E{episode_num:02d} 存在于 TMDB"
        ))
        await notify_log_update()

        if not await self._check_emby_before_output(
            result=result,
            series=series,
            tmdb_id=result.selected_id,
            season=season_num,
            episode=episode_num,
            scrape_logs=scrape_logs,
            notify_log_update=notify_log_update,
        ):
            return result

        return await self._execute_scrape_output(
            request=request,
            result=result,
            series=series,
            season_info=season_info,
            season=season_num,
            episode=episode_num,
            scrape_logs=scrape_logs,
            notify_log_update=notify_log_update,
        )

    async def scrape_by_id(
        self,
        request: ScrapeByIdRequest,
        on_log_update: LogUpdateCallback | None = None,
    ) -> ScrapeResult:
        """Scrape file with manually specified TMDB ID.

        Use this when automatic search fails.

        Args:
            request: Request with file path and TMDB ID.
            on_log_update: Optional callback for real-time log updates.

        Returns:
            ScrapeResult with operation status.
        """
        try:
            self._validate_metadata_directory(request.metadata_dir, request.metadata_locator)
        except PathSecurityError as exc:
            return ScrapeResult(file_path=request.file_path, status=ScrapeStatus.MOVE_FAILED, message=str(exc))
        file_path = request.file_path
        path = Path(file_path)
        scrape_logs: list[ScrapeLogStep] = []
        manual_parsed_title = (
            self.parser_service.parse(path.name, file_path).series_name
            if self.parser_service is not None
            else None
        )

        async def notify_log_update():
            """通知日志更新。"""
            if on_log_update:
                await on_log_update(scrape_logs)

        if not self._is_provider_source(request.file_locator):
            try:
                path = validate_media_path(
                    file_path,
                    must_exist=True,
                    require_file=True,
                )
            except PathSecurityError as exc:
                return ScrapeResult(
                    file_path=file_path,
                    status=ScrapeStatus.MOVE_FAILED,
                    message=str(exc),
                )

        result = ScrapeResult(
            file_path=file_path,
            status=ScrapeStatus.SUCCESS,
            selected_id=request.tmdb_id,
            parsed_season=request.season,
            parsed_episode=request.episode,
        )

        # Step 1: 获取剧集详情
        detail_step = ScrapeLogStep(name="获取详情", logs=[])
        detail_step.logs.append(ScrapeLogEntry(message=f"TMDB ID: {request.tmdb_id}, S{request.season:02d}E{request.episode:02d}"))
        scrape_logs.append(detail_step)
        await notify_log_update()

        try:
            series = await self.tmdb_service.get_series_by_api(request.tmdb_id)
            if series is None:
                detail_step.logs.append(ScrapeLogEntry(message="无法获取剧集详情", level=LogLevel.ERROR))
                detail_step.completed = False
                await notify_log_update()
                result.status = ScrapeStatus.API_FAILED
                result.message = f"无法获取剧集详情: ID {request.tmdb_id}"
                result.scrape_logs = scrape_logs
                return result
            result.series_info = series
            detail_step.logs.append(ScrapeLogEntry(message=f"剧集名称: {series.name}"))
            await notify_log_update()
        except (TMDBError, ValueError) as e:
            detail_step.logs.append(ScrapeLogEntry(message=str(e), level=LogLevel.ERROR))
            detail_step.completed = False
            await notify_log_update()
            result.status = ScrapeStatus.API_FAILED
            result.message = str(e)
            result.scrape_logs = scrape_logs
            return result
        except (httpx.TimeoutException, httpx.RequestError) as e:
            detail_step.logs.append(ScrapeLogEntry(message=f"TMDB API 请求失败: {str(e)}", level=LogLevel.ERROR))
            detail_step.completed = False
            await notify_log_update()
            result.status = ScrapeStatus.API_FAILED
            result.message = f"TMDB API 请求失败: {str(e)}"
            result.scrape_logs = scrape_logs
            return result

        # Get season details (for episode info)
        season_info = None
        try:
            season_info = await self.tmdb_service.get_season_by_api(
                request.tmdb_id, request.season
            )
            detail_step.logs.append(ScrapeLogEntry(message=f"获取季度详情: 共 {len(season_info.episodes) if season_info and season_info.episodes else 0} 集"))
            await notify_log_update()
        except Exception as e:
            detail_step.logs.append(ScrapeLogEntry(message=f"获取季度详情失败: {e}", level=LogLevel.ERROR))
            detail_step.completed = False
            await notify_log_update()
            result.status = ScrapeStatus.API_FAILED
            result.message = f"获取季度详情失败: {e}"
            result.scrape_logs = scrape_logs
            return result

        # 核验 TMDB 中是否存在该季和该集（scrape_by_id 路径）
        season_exists = any(s.season_number == request.season for s in series.seasons)
        episode_exists = (
            season_info is not None
            and bool(season_info.episodes)
            and any(e.episode_number == request.episode for e in season_info.episodes)
        )
        if not season_exists or not episode_exists:
            reasons = []
            if not season_exists:
                reasons.append(f"TMDB 中不存在第 {request.season} 季")
            if season_exists and not episode_exists:
                reasons.append(f"TMDB 第 {request.season} 季中不存在第 {request.episode} 集")
            reason_text = "，".join(reasons)
            detail_step.logs.append(ScrapeLogEntry(
                message=f"核验失败: {reason_text}", level=LogLevel.WARNING,
            ))
            detail_step.completed = False
            await notify_log_update()
            result.status = ScrapeStatus.NEED_SEASON_EPISODE
            result.message = f"需要确认季/集: {reason_text}"
            result.series_info = series
            result.parsed_season = request.season
            result.parsed_episode = request.episode
            result.scrape_logs = scrape_logs
            return result

        detail_step.logs.append(ScrapeLogEntry(
            message=f"核验通过: S{request.season:02d}E{request.episode:02d} 存在于 TMDB"
        ))
        await notify_log_update()

        if not await self._check_emby_before_output(
            result=result,
            series=series,
            tmdb_id=request.tmdb_id,
            season=request.season,
            episode=request.episode,
            scrape_logs=scrape_logs,
            notify_log_update=notify_log_update,
            skip=request.skip_emby_check,
        ):
            return result

        return await self._execute_scrape_output(
            request=request,
            result=result,
            series=series,
            season_info=season_info,
            season=request.season,
            episode=request.episode,
            scrape_logs=scrape_logs,
            notify_log_update=notify_log_update,
            file_action=request.file_action,
            remember_manual_alias=True,
            parsed_title=manual_parsed_title,
        )

    async def batch_scrape(self, request: BatchScrapeRequest) -> BatchScrapeResponse:
        """Batch scrape multiple files.

        Args:
            request: Batch request with file paths.

        Returns:
            BatchScrapeResponse with all results.
        """
        results: list[ScrapeResult] = []

        for file_path in request.file_paths:
            if request.dry_run:
                # Preview only
                preview = await self.preview(file_path)
                results.append(
                    ScrapeResult(
                        file_path=file_path,
                        status=ScrapeStatus.SUCCESS,
                        parsed_title=preview.parsed_title,
                        parsed_season=preview.parsed_season,
                        parsed_episode=preview.parsed_episode,
                        search_results=preview.search_results,
                    )
                )
            else:
                scrape_request = ScrapeRequest(
                    file_path=file_path,
                    output_dir=request.output_dir,
                    auto_select=request.auto_select,
                )
                result = await self.scrape_file(scrape_request)
                results.append(result)

        success_count = sum(1 for r in results if r.status == ScrapeStatus.SUCCESS)
        failed_count = len(results) - success_count

        return BatchScrapeResponse(
            total=len(results),
            success=success_count,
            failed=failed_count,
            results=results,
        )
