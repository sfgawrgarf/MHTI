"""Scraper service for orchestrating the scraping workflow.

该模块是刮削工作流的核心编排器，能力实现由组合式协作者提供：
- ScraperConfigResolver: 配置检查与有效配置
- ScraperMetadataResolver: 元数据处理（NFO、搜索结果）
- ScraperMediaPipeline: 媒体文件处理（图片、字幕、Emby）
- P115StorageProvider: 115 网盘输出适配
"""

import logging
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx

from server.application.ai_provider_service import AIProviderError, AIProviderService
from server.application.file_io import run_file_io
from server.application.media_alias_service import MediaAliasMatch, MediaAliasService
from server.application.media_identity_service import MediaIdentityService
from server.application.recognition import build_search_title_variants
from server.application.scraping.config_resolver import ScraperConfigResolver
from server.application.scraping.media_pipeline import ScraperMediaPipeline
from server.application.scraping.metadata_resolver import ScraperMetadataResolver
from server.application.scraping.output_writer import OutputWriter, _get_mode_name
from server.application.scraping.p115_storage_provider import P115StorageProvider
from server.common.path_security import PathSecurityError, validate_media_path
from server.domain.artifacts.image_service import ImageService
from server.domain.artifacts.nfo_service import NFOService
from server.domain.artifacts.rename_service import RenameService
from server.domain.artifacts.subtitle_service import SubtitleService
from server.infrastructure.log_security import safe_log_value
from server.domain.integration.emby_service import EmbyService
from server.domain.metadata.tmdb_service import TMDBService
from server.domain.parsing.parser_service import ParserService
from server.domain.system.config_service import ConfigService
from server.infrastructure.db import DATABASE_PATH
from server.models.ai import AICandidate, AIUsageMode
from server.models.emby import ConflictCheckResult, ConflictType
from server.models.history import ScrapeLogLevel, ScrapeLogEntry, ScrapeLogStep
from server.models.manual_job import ManualJobAdvancedSettings
from server.models.nfo import SeasonNFO
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
from server.models.storage import (
    StorageLocator,
    StorageProvider,
    validate_locator_namespace,
)
from server.models.tmdb import TMDBSearchResult, TMDBSeason, TMDBSeries

logger = logging.getLogger(__name__)

# 日志更新回调类型
LogUpdateCallback = Callable[[list[ScrapeLogStep]], Awaitable[None]]
# 匹配已确定（TMDB ID + 季 + 集）时的回调：调用方据此落库，
# 超时/失败前已确定的匹配才不会丢（重试时直接沿用，不必重新搜索）
MatchResolvedCallback = Callable[[int, int, int], Awaitable[None]]


class ScraperService:
    """Service for orchestrating the complete scraping workflow.

    编排 parse -> search -> verify -> NFO -> move -> images -> subtitles 全流程；
    能力实现委托给注入的协作者（见模块 docstring）。
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
        self.ai_provider = AIProviderService(config_service)
        # The alias service currently uses the process-wide database context.
        # Do not let a scraper bound to an isolated test/migration database read
        # or mutate the production alias table.
        self.media_alias_service = (
            MediaAliasService()
            if getattr(config_service, "db_path", DATABASE_PATH) == DATABASE_PATH
            else None
        )

        self._config_resolver = ScraperConfigResolver(config_service, tmdb_service)
        self._metadata_resolver = ScraperMetadataResolver(tmdb_service, nfo_service)
        self._media_pipeline = ScraperMediaPipeline(image_service, subtitle_service, emby_service)
        self._output_writer = OutputWriter(self)

    async def _record_success_identity(
        self,
        *,
        source_path: str,
        target_path: str | None,
        series: TMDBSeries,
        season: int,
        episode: int,
        parsed_title: str | None,
    ) -> None:
        """Persist version and confirmed alias data after a successful output."""
        try:
            await MediaIdentityService().record(
                file_path=source_path,
                target_path=target_path,
                tmdb_id=series.id,
                season=season,
                episode=episode,
                title=series.name,
            )
        except Exception as exc:  # registration must not undo a successful scrape
            logger.warning("记录媒体版本失败: %s", exc)
        try:
            if self.media_alias_service is None:
                return
            await self.media_alias_service.remember_confirmed(
                file_path=source_path,
                parsed_title=parsed_title,
                tmdb_id=series.id,
                season=season,
                episode=episode,
                canonical_titles=[series.name, series.original_name],
                source="scrape_success",
            )
        except Exception as exc:
            logger.warning("记录媒体别名失败: %s", exc)

    # ---- 配置检查与有效配置（转发到 ScraperConfigResolver，保持原方法面）----

    async def check_config(self) -> tuple[bool, str | None]:
        return await self._config_resolver.check_config()

    async def ensure_config_ready(self) -> None:
        await self._config_resolver.ensure_config_ready()

    async def ensure_api_token_ready(self) -> None:
        await self._config_resolver.ensure_api_token_ready()

    async def _get_effective_download_config(
        self,
        advanced_settings: ManualJobAdvancedSettings | None,
    ) -> dict:
        return await self._config_resolver.get_effective_download_config(advanced_settings)

    async def _get_effective_nfo_config(
        self,
        advanced_settings: ManualJobAdvancedSettings | None,
    ) -> dict:
        return await self._config_resolver.get_effective_nfo_config(advanced_settings)

    async def _get_effective_image_config(
        self,
        advanced_settings: ManualJobAdvancedSettings | None,
    ) -> dict:
        # Keep the historical four-key method as the compatibility seam used
        # by integrations/tests while adding the complete image settings.
        legacy = await self._get_effective_download_config(advanced_settings)
        try:
            config = await self._config_resolver.get_effective_image_config(
                advanced_settings
            )
            if not isinstance(config, dict):
                raise TypeError("图片配置不是字典")
        except (AttributeError, TypeError, ValueError):
            # Some isolated callers intentionally provide only the legacy
            # configuration seam. Keep those calls image-free safely.
            config = {
                "series_poster": False,
                "series_backdrop": False,
                "series_logo": False,
                "series_banner": False,
                "season_poster": False,
                "episode_thumb": False,
                "extra_backdrops": False,
                "extra_backdrops_count": 0,
                "overwrite_existing": False,
            }
        config.update(
            {
                "series_poster": legacy.get(
                    "download_poster", config.get("series_poster", False)
                ),
                "series_backdrop": legacy.get(
                    "download_fanart", config.get("series_backdrop", False)
                ),
                "episode_thumb": legacy.get(
                    "download_thumb", config.get("episode_thumb", False)
                ),
                "overwrite_existing": legacy.get(
                    "overwrite_existing", config.get("overwrite_existing", False)
                ),
            }
        )
        return config

    async def _get_effective_naming_config(
        self,
        advanced_settings: ManualJobAdvancedSettings | None,
    ):
        return await self._config_resolver.get_effective_naming_config(advanced_settings)

    async def _sanitize_scrape_filename(
        self,
        filename: str,
        advanced_settings: ManualJobAdvancedSettings | None = None,
    ) -> str:
        from server.application.organize_filters import OrganizeFilter

        config = await self._config_resolver.get_effective_organize_config(
            advanced_settings
        )
        if config is None:
            return filename
        return OrganizeFilter.from_global_config(config).sanitize_filename(filename)

    # ---- 元数据/NFO（转发到 ScraperMetadataResolver，保持原方法面）----

    async def _enrich_search_results(
        self,
        results: list[TMDBSearchResult],
    ) -> list[TMDBSearchResult]:
        return await self._metadata_resolver.enrich_search_results(results)

    def _select_best_match(
        self,
        results: list[TMDBSearchResult],
        query: str,
    ) -> TMDBSearchResult:
        return self._metadata_resolver.select_best_match(results, query)

    def _generate_episode_nfo(
        self,
        series: TMDBSeries,
        season_num: int,
        episode_num: int,
        season_info: TMDBSeason | None = None,
        fields: Mapping[str, bool] | None = None,
    ) -> str:
        return self._metadata_resolver.generate_episode_nfo(
            series, season_num, episode_num, season_info, fields
        )

    def _get_season_nfo_data(self, series: TMDBSeries, season_num: int) -> SeasonNFO:
        return self._metadata_resolver.get_season_nfo_data(series, season_num)

    # ---- 图片/字幕/Emby（转发到 ScraperMediaPipeline，保持原方法面）----

    async def _download_series_images(
        self,
        series: TMDBSeries,
        series_folder: str,
        download_poster: bool = True,
        download_fanart: bool = True,
        image_config: Mapping[str, object] | None = None,
    ) -> None:
        await self._media_pipeline.download_series_images(
            series,
            series_folder,
            download_poster,
            download_fanart,
            image_config=image_config,
        )

    async def _download_episode_image(
        self,
        season_info: TMDBSeason | None,
        season_num: int,
        episode_num: int,
        season_folder: str,
        video_stem: str,
        image_config: Mapping[str, object] | None = None,
    ) -> None:
        await self._media_pipeline.download_episode_image(
            season_info,
            season_num,
            episode_num,
            season_folder,
            video_stem,
            image_config=image_config,
        )

    async def _download_season_image(
        self,
        season_info: TMDBSeason | None,
        season_num: int,
        season_folder: str,
        image_config: Mapping[str, object],
    ) -> None:
        await self._media_pipeline.download_season_image(
            season_info,
            season_num,
            season_folder,
            image_config,
        )

    def _process_subtitles(self, source_video_path: str, dest_video_path: str,
                           link_mode=None, conflict_action=None) -> list[str]:
        return self._media_pipeline.process_subtitles(
            source_video_path, dest_video_path, link_mode, conflict_action
        )

    async def _check_emby_conflict(
        self,
        series_name: str,
        tmdb_id: int | None,
        season: int,
        episode: int,
    ) -> ConflictCheckResult:
        return await self._media_pipeline.check_emby_conflict(
            series_name, tmdb_id, season, episode
        )

    def _is_provider_source(self, locator: StorageLocator | None) -> bool:
        """判断是否为云端 provider 文件。"""
        return locator is not None and locator.provider != StorageProvider.LOCAL

    @staticmethod
    def _validate_storage_locators(request) -> None:
        """Reject provider/path namespace mismatches before any work starts."""
        for locator in (
            request.file_locator,
            request.output_locator,
            request.metadata_locator,
        ):
            if locator is None:
                continue
            try:
                validate_locator_namespace(locator)
            except ValueError as exc:
                raise PathSecurityError(str(exc)) from exc

    def _get_storage_provider(self, provider: StorageProvider):
        """按 provider 返回对应存储适配器。"""
        if provider == StorageProvider.P115:
            return P115StorageProvider(self.config_service)
        raise ValueError(f"不支持的存储提供方: {provider}")

    async def _resolve_p115_output_locator(self, locator: StorageLocator) -> StorageLocator:
        """输出目录缺少 file_id 时，按路径解析出真实的 115 目录 id。"""
        return await self._output_writer.resolve_p115_output_locator(locator)

    def _auto_correct_season(
        self,
        requested_season: int,
        series: TMDBSeries,
    ) -> tuple[int, str | None]:
        """根据 TMDB 实际季数自动修正季号。

        规则：
        - 计算实际可用季（排除季0的空 specials）
        - 若请求的季号存在 → 保持不变
        - 若不存在且可用季 <= 2 → 自动选第一个可用季（通常为1），返回修正说明
        - 若不存在且可用季 > 2 → 保持原值（让后续核验拦截，交给用户选）

        返回 (修正后的季号, 修正说明或 None)。
        """
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

    # ---- 存储输出/本地整理（转发到 OutputWriter，保持原方法面）----

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
        conflict_action: str | None = None,
        naming_template=None,
    ) -> RenameRequest:
        return self._output_writer.build_rename_request(
            source_path=source_path,
            title=title,
            season=season,
            episode=episode,
            output_dir=output_dir,
            link_mode=link_mode,
            year=year,
            conflict_action=conflict_action,
            naming_template=naming_template,
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
        return await self._output_writer.finalize_storage_output(
            file_locator=file_locator,
            output_locator=output_locator,
            metadata_locator=metadata_locator,
            link_mode=link_mode,
            title=title,
            season=season,
            episode=episode,
            source_path=source_path,
            year=year,
            advanced_settings=advanced_settings,
        )

    async def _write_local_metadata_only(
        self,
        *,
        title: str,
        season: int,
        episode: int,
        year: int | None,
        metadata_dir: str | None,
        output_dir_for_preview: str,
        nfo_content: str,
        series,
        season_info,
        move_step: ScrapeLogStep,
        notify_log_update,
        link_mode: OrganizeMode | None,
        advanced_settings: ManualJobAdvancedSettings | None = None,
        dest_path_override: Path | str | None = None,
        require_metadata_dir: bool = True,
    ) -> tuple[str, Path, Path]:
        return await self._output_writer.write_local_metadata_only(
            title=title,
            season=season,
            episode=episode,
            year=year,
            metadata_dir=metadata_dir,
            output_dir_for_preview=output_dir_for_preview,
            nfo_content=nfo_content,
            series=series,
            season_info=season_info,
            move_step=move_step,
            notify_log_update=notify_log_update,
            link_mode=link_mode,
            advanced_settings=advanced_settings,
            dest_path_override=dest_path_override,
            require_metadata_dir=require_metadata_dir,
        )

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
        series,
        season_info,
        mode_name: str,
        move_step: ScrapeLogStep,
        notify_log_update: Callable[[], Awaitable[None]],
        result: ScrapeResult,
        advanced_settings: ManualJobAdvancedSettings | None = None,
    ) -> tuple[Path, Path, Path]:
        """Write metadata at the resolved destination before publishing media."""
        resolved_dest_path = await run_file_io(
            self.rename_service.resolve_destination_path,
            rename_request,
        )
        resolved_dest_path = Path(resolved_dest_path)
        source_path = Path(rename_request.source_path)
        if (
            resolved_dest_path != source_path
            and (resolved_dest_path.exists() or resolved_dest_path.is_symlink())
            and rename_request.conflict_action != "overwrite"
        ):
            result.status = ScrapeStatus.FILE_CONFLICT
            result.message = f"目标文件已存在: {resolved_dest_path}"
            raise FileExistsError(str(resolved_dest_path))

        await self._write_local_metadata_only(
            title=title,
            season=season,
            episode=episode,
            year=year,
            metadata_dir=metadata_dir,
            output_dir_for_preview=output_dir_display or str(resolved_dest_path.parent),
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
        return await self._organize_local_output(
            rename_request=rename_request,
            source_display_path=source_display_path,
            output_dir_display=output_dir_display,
            mode_name=mode_name,
            move_step=move_step,
            notify_log_update=notify_log_update,
            result=result,
        )

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
        """Persist a published media version without changing output status."""
        if tmdb_id is None or not target_path:
            return
        await MediaIdentityService().record(
            file_path=file_path,
            target_path=target_path,
            tmdb_id=tmdb_id,
            season=season,
            episode=episode,
            title=title,
        )

    @staticmethod
    async def _safe_notify_log_update(
        notify_log_update: Callable[[], Awaitable[None]],
        stage: str,
    ) -> None:
        """A post-publication log failure must not cause media retry."""
        try:
            await notify_log_update()
        except Exception:
            logger.exception("%s写入失败；媒体输出状态保持成功", stage)

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
        """Finalize a successful output and isolate non-media audit failures."""
        if season_info and season_info.episodes:
            result.episode_info = next(
                (item for item in season_info.episodes if item.episode_number == episode),
                None,
            )

        warnings: list[str] = []
        try:
            await self._record_media_version(
                file_path=file_path,
                target_path=result.dest_path,
                tmdb_id=tmdb_id,
                season=season,
                episode=episode,
                title=getattr(series, "name", None),
            )
        except Exception:
            logger.exception("媒体已输出，但媒体版本记录写入失败: %s", result.dest_path)
            warnings.append("媒体版本记录写入失败")

        if remember_manual_alias and self.media_alias_service is not None:
            try:
                await self.media_alias_service.remember_confirmed(
                    file_path=file_path,
                    parsed_title=parsed_title,
                    tmdb_id=tmdb_id,
                    season=season,
                    episode=episode,
                    canonical_titles=[
                        getattr(series, "name", None),
                        getattr(series, "original_name", None),
                    ],
                    source="manual",
                )
            except Exception:
                logger.exception("媒体已输出，但手动别名记录写入失败")
                warnings.append("手动别名记录写入失败")

        if warnings and scrape_logs:
            scrape_logs[-1].logs.append(
                ScrapeLogEntry(
                    message="；".join(warnings),
                    level=ScrapeLogLevel.WARNING,
                )
            )
        result.status = ScrapeStatus.SUCCESS
        result.message = "刮削完成"
        result.scrape_logs = scrape_logs
        await self._safe_notify_log_update(notify_log_update, "任务完成日志")
        return result

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
        return self._output_writer.resolve_move_input(
            file_path=file_path,
            file_locator=file_locator,
            output_dir=output_dir,
            output_locator=output_locator,
            metadata_dir=metadata_dir,
            metadata_locator=metadata_locator,
        )

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
    ) -> tuple[Path, Path, Path]:
        return await self._output_writer.organize_local_output(
            rename_request=rename_request,
            source_display_path=source_display_path,
            output_dir_display=output_dir_display,
            mode_name=mode_name,
            move_step=move_step,
            notify_log_update=notify_log_update,
            result=result,
        )

    async def _resolve_metadata_folders(
        self,
        *,
        dest_file: Path,
        season_folder: Path,
        series_folder: Path,
        metadata_dir: str | None,
    ) -> tuple[Path, Path]:
        return await self._output_writer.resolve_metadata_folders(
            dest_file=dest_file,
            season_folder=season_folder,
            series_folder=series_folder,
            metadata_dir=metadata_dir,
        )

    async def _cleanup_source_parent_if_enabled(
        self,
        *,
        request: ScrapeRequest | ScrapeByIdRequest,
        source_path: str,
    ) -> None:
        """Clean one empty local source directory after a successful move."""
        settings = request.advanced_settings
        if (
            settings is None
            or not settings.delete_empty_parent
            or request.link_mode not in (None, OrganizeMode.MOVE)
            or self._is_provider_source(request.file_locator)
        ):
            return

        from server.application.source_cleanup import remove_empty_source_parent

        try:
            removed = await run_file_io(remove_empty_source_parent, source_path)
            if removed:
                logger.info(
                    "已清理空源目录: %s",
                    safe_log_value(Path(source_path).parent),
                )
        except (OSError, PathSecurityError) as exc:
            # Cleanup is best-effort and must never turn a completed move into
            # a failed scrape.
            logger.warning(
                "清理空源目录失败 %s: %s",
                safe_log_value(Path(source_path).parent),
                safe_log_value(exc),
            )

    async def _move_and_finalize(
        self,
        *,
        request: ScrapeRequest | ScrapeByIdRequest,
        result: ScrapeResult,
        scrape_logs: list[ScrapeLogStep],
        notify_log_update: Callable[[], Awaitable[None]],
        series: TMDBSeries,
        season_num: int,
        episode_num: int,
        season_info: TMDBSeason | None,
        nfo_content: str,
        notify_after_move_step: bool = False,
    ) -> ScrapeResult:
        """共享尾部：文件移动/整理（本地/115）、NFO/图片落盘、字幕处理与状态回写。

        scrape_file 与 scrape_by_id 共用；两处既有差异经参数保留：
        - 季/集号由调用方解析后传入（文件名解析 vs 手动指定）
        - notify_after_move_step：scrape_by_id 在移动步骤创建后额外刷新一次日志
        """
        file_path = request.file_path
        mode_name = _get_mode_name(request.link_mode)
        move_step = ScrapeLogStep(name=f"{mode_name}文件", logs=[])
        scrape_logs.append(move_step)
        if notify_after_move_step:
            await notify_log_update()
        try:
            year = series.first_air_date.year if series.first_air_date else None
            naming_template = await self._get_effective_naming_config(
                request.advanced_settings
            )
            source_display_path, effective_output_dir, effective_metadata_dir = self._resolve_move_input(
                file_path=file_path,
                file_locator=request.file_locator,
                output_dir=request.output_dir,
                output_locator=request.output_locator,
                metadata_dir=request.metadata_dir,
                metadata_locator=request.metadata_locator,
            )

            file_action = getattr(request, "file_action", None)
            if (
                file_action is None
                and request.advanced_settings is not None
                and not request.advanced_settings.use_global_organize
                and request.advanced_settings.overwrite_video
            ):
                file_action = "overwrite"

            should_process_subtitles = (
                request.advanced_settings is None
                or request.advanced_settings.process_subtitle
            )

            if request.file_locator and request.output_locator:
                move_step.logs.append(ScrapeLogEntry(message=f"源文件: {source_display_path}"))
                move_step.logs.append(ScrapeLogEntry(message=f"目标目录: {request.output_locator.path}"))
                move_step.logs.append(ScrapeLogEntry(message=f"整理模式: {mode_name}"))
                await notify_log_update()

                if request.output_locator.provider == StorageProvider.P115:
                    dest_locator = await self._finalize_storage_output(
                        file_locator=request.file_locator,
                        output_locator=request.output_locator,
                        metadata_locator=request.metadata_locator,
                        link_mode=request.link_mode,
                        title=series.name,
                        season=season_num,
                        episode=episode_num,
                        source_path=source_display_path,
                        year=year,
                        advanced_settings=request.advanced_settings,
                    )
                    result.dest_path = dest_locator.path
                    move_step.logs.append(ScrapeLogEntry(message=f"文件{mode_name}成功: {dest_locator.path}"))
                    await notify_log_update()
                    move_step.logs.append(ScrapeLogEntry(message="115 网盘视频已输出，开始生成本地元数据"))
                    await notify_log_update()
                    # 115→115：视频在 115，NFO/图片/字幕留本地
                    nfo_path_str, _, _ = await self._write_local_metadata_only(
                        title=series.name,
                        season=season_num,
                        episode=episode_num,
                        year=year,
                        metadata_dir=effective_metadata_dir,
                        output_dir_for_preview=effective_output_dir,
                        nfo_content=nfo_content,
                        series=series,
                        season_info=season_info,
                        move_step=move_step,
                        notify_log_update=notify_log_update,
                        link_mode=request.link_mode,
                        advanced_settings=request.advanced_settings,
                    )
                    result.nfo_path = nfo_path_str or None
                    result.status = ScrapeStatus.SUCCESS
                    result.message = "刮削完成"
                    result.scrape_logs = scrape_logs
                    await self._record_success_identity(
                        source_path=file_path,
                        target_path=result.dest_path,
                        series=series,
                        season=season_num,
                        episode=episode_num,
                        parsed_title=result.parsed_title,
                    )
                    await notify_log_update()
                    return result

                if request.output_locator.provider == StorageProvider.LOCAL:
                    provider = self._get_storage_provider(request.file_locator.provider)
                    with TemporaryDirectory(prefix="mhti-115-download-") as temp_dir:
                        downloaded_path = await provider.download(request.file_locator, Path(temp_dir))
                        local_source_path = str(downloaded_path)
                        should_process_subtitles = False

                        rename_request = self._build_rename_request(
                            source_path=local_source_path,
                            title=series.name,
                            season=season_num,
                            episode=episode_num,
                            year=year,
                            output_dir=effective_output_dir,
                            link_mode=request.link_mode,
                            conflict_action=file_action,
                            naming_template=naming_template,
                        )

                        dest_file, season_folder, series_folder = await self._organize_local_output(
                            rename_request=rename_request,
                            source_display_path=source_display_path,
                            output_dir_display=effective_output_dir,
                            mode_name=mode_name,
                            move_step=move_step,
                            notify_log_update=notify_log_update,
                            result=result,
                        )
                else:
                    local_source_path = source_display_path
            else:
                local_source_path = source_display_path

            if not (request.file_locator and request.output_locator and request.output_locator.provider == StorageProvider.LOCAL):
                rename_request = self._build_rename_request(
                    source_path=local_source_path,
                    title=series.name,
                    season=season_num,
                    episode=episode_num,
                    year=year,
                    output_dir=effective_output_dir,
                    link_mode=request.link_mode,
                    conflict_action=file_action,
                    naming_template=naming_template,
                )

                dest_file, season_folder, series_folder = await self._organize_local_output(
                    rename_request=rename_request,
                    source_display_path=source_display_path,
                    output_dir_display=effective_output_dir,
                    mode_name=mode_name,
                    move_step=move_step,
                    notify_log_update=notify_log_update,
                    result=result,
                )
            metadata_series_folder, metadata_season_folder = await self._resolve_metadata_folders(
                dest_file=dest_file,
                season_folder=season_folder,
                series_folder=series_folder,
                metadata_dir=effective_metadata_dir,
            )

            # Write episode NFO file (if enabled)
            nfo_config = await self._get_effective_nfo_config(request.advanced_settings)
            if nfo_config["nfo_enabled"]:
                episode_fields = nfo_config.get("episode") or {}
                if episode_fields.get("enabled", True):
                    nfo_path = validate_media_path(
                        str(metadata_season_folder / f"{dest_file.stem}.nfo")
                    )
                    nfo_path.write_text(nfo_content, encoding="utf-8")
                    result.nfo_path = str(nfo_path)
                    move_step.logs.append(ScrapeLogEntry(message=f"NFO 文件已写入: {nfo_path}"))

                # 生成 tvshow.nfo（剧集信息）到剧集文件夹
                if (nfo_config.get("tvshow") or {}).get("enabled", True):
                    tvshow_nfo_path = validate_media_path(
                        str(metadata_series_folder / "tvshow.nfo")
                    )
                    if not tvshow_nfo_path.exists():
                        metadata_series_folder.mkdir(parents=True, exist_ok=True)
                        tvshow_nfo_data = self.nfo_service.tvshow_from_tmdb(series)
                        tvshow_nfo_content = self.nfo_service.generate_tvshow_nfo(
                            tvshow_nfo_data,
                            fields=nfo_config.get("tvshow"),
                        )
                        tvshow_nfo_path.write_text(tvshow_nfo_content, encoding="utf-8")
                        move_step.logs.append(ScrapeLogEntry(message="tvshow.nfo 已生成"))

                # 生成 season.nfo 到季度文件夹
                if (nfo_config.get("season") or {}).get("enabled", True):
                    season_nfo_path = validate_media_path(
                        str(metadata_season_folder / "season.nfo")
                    )
                    if not season_nfo_path.exists():
                        season_nfo_data = self._get_season_nfo_data(series, season_num)
                        season_nfo_content = self.nfo_service.generate_season_nfo(
                            season_nfo_data,
                            fields=nfo_config.get("season"),
                        )
                        season_nfo_path.write_text(season_nfo_content, encoding="utf-8")
                        move_step.logs.append(ScrapeLogEntry(message="season.nfo 已生成"))
            else:
                move_step.logs.append(ScrapeLogEntry(message="NFO 生成已跳过（配置禁用）"))

            await notify_log_update()

            # 下载图片 (based on config)
            image_step = ScrapeLogStep(name="下载图片", logs=[])
            scrape_logs.append(image_step)
            await notify_log_update()

            image_config = await self._get_effective_image_config(request.advanced_settings)

            # 下载剧集封面和背景图到元数据剧集文件夹
            if any(
                image_config.get(key)
                for key in (
                    "series_poster",
                    "series_backdrop",
                    "series_logo",
                    "series_banner",
                    "extra_backdrops",
                )
            ):
                await self._download_series_images(
                    series,
                    str(metadata_series_folder),
                    download_poster=bool(image_config["series_poster"]),
                    download_fanart=bool(image_config["series_backdrop"]),
                    image_config=image_config,
                )
                image_step.logs.append(ScrapeLogEntry(message="剧集图片处理完成"))
            else:
                image_step.logs.append(ScrapeLogEntry(message="剧集图片下载已跳过（配置禁用）"))
            await notify_log_update()

            # 下载集封面图到元数据季度文件夹
            if image_config.get("season_poster"):
                await self._download_season_image(
                    season_info,
                    season_num,
                    str(metadata_season_folder),
                    image_config,
                )
            if image_config["episode_thumb"]:
                await self._download_episode_image(
                    season_info,
                    season_num,
                    episode_num,
                    str(metadata_season_folder),
                    dest_file.stem,
                    image_config=image_config,
                )
                image_step.logs.append(ScrapeLogEntry(message="集封面图处理完成"))
            else:
                image_step.logs.append(ScrapeLogEntry(message="集封面图下载已跳过（配置禁用）"))
            await notify_log_update()

            # 处理关联字幕文件
            if should_process_subtitles:
                await run_file_io(self._process_subtitles, local_source_path, str(dest_file),
                                  request.link_mode, file_action)
            await self._cleanup_source_parent_if_enabled(
                request=request,
                source_path=source_display_path,
            )

        except FileExistsError:
            result.scrape_logs = scrape_logs
            return result
        except Exception as e:
            move_step.logs.append(ScrapeLogEntry(message=f"文件{mode_name}失败: {str(e)}", level=ScrapeLogLevel.ERROR))
            move_step.completed = False
            await notify_log_update()
            result.status = ScrapeStatus.MOVE_FAILED
            result.message = f"文件{mode_name}失败: {str(e)}"
            result.scrape_logs = scrape_logs
            return result

        # 设置集信息
        if season_info and season_info.episodes:
            for ep in season_info.episodes:
                if ep.episode_number == episode_num:
                    result.episode_info = ep
                    break

        result.status = ScrapeStatus.SUCCESS
        result.message = "刮削完成"
        result.scrape_logs = scrape_logs
        await self._record_success_identity(
            source_path=file_path,
            target_path=result.dest_path,
            series=series,
            season=season_num,
            episode=episode_num,
            parsed_title=result.parsed_title,
        )
        await notify_log_update()
        return result

    async def preview(self, file_path: str) -> ScrapePreview:
        """Preview scrape operation without executing.

        Args:
            file_path: Path to the video file.

        Returns:
            ScrapePreview with parsed info and search results.
        """
        path = validate_media_path(file_path, must_exist=True, require_file=True)

        # Parse filename
        parsed_filename = await self._sanitize_scrape_filename(path.name)
        parsed = self.parser_service.parse(parsed_filename, file_path)

        preview = ScrapePreview(
            file_path=file_path,
            parsed_title=parsed.series_name,
            parsed_season=parsed.season,
            parsed_episode=parsed.episode,
        )

        # Search TMDB if we have a title
        if parsed.series_name:
            try:
                search_response = await self.tmdb_service.search_series_by_api(parsed.series_name)
                preview.search_results = search_response.results
            except (httpx.TimeoutException, httpx.RequestError):
                pass

        return preview

    async def scrape_file(
        self,
        request: ScrapeRequest,
        on_log_update: LogUpdateCallback | None = None,
        on_match_resolved: MatchResolvedCallback | None = None,
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
        self._validate_storage_locators(request)
        file_path = request.file_path
        path = Path(file_path)
        scrape_logs: list[ScrapeLogStep] = []

        async def notify_log_update():
            """通知日志更新。"""
            if on_log_update:
                await on_log_update(scrape_logs)

        async def notify_match_resolved(season: int, episode: int):
            """通知「匹配已确定」（核验通过、后续不会再变）。"""
            if on_match_resolved and result.selected_id:
                await on_match_resolved(result.selected_id, season, episode)

        # Validate local sources before any metadata lookup or file operation.
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
        parsed_filename = await self._sanitize_scrape_filename(
            path.name,
            request.advanced_settings,
        )
        parsed = self.parser_service.parse(parsed_filename, file_path)

        result = ScrapeResult(
            file_path=file_path,
            status=ScrapeStatus.SUCCESS,
            parsed_title=parsed.series_name,
            parsed_season=parsed.season,
            parsed_episode=parsed.episode,
        )

        if not parsed.series_name:
            parse_step.logs.append(ScrapeLogEntry(message="无法从文件名解析出剧集名称", level=ScrapeLogLevel.ERROR))
            parse_step.completed = False
            scrape_logs.append(parse_step)
            await notify_log_update()
            result.status = ScrapeStatus.NO_MATCH
            result.message = "无法从文件名解析出剧集名称"
            result.scrape_logs = scrape_logs
            return result

        parse_step.logs.append(ScrapeLogEntry(message=f"解析结果: {parsed.series_name} S{parsed.season or '?'}E{parsed.episode or '?'}"))
        scrape_logs.append(parse_step)
        await notify_log_update()

        # A confirmed local alias is stronger than a fuzzy title search and
        # lets repeat scrapes keep the user's prior TMDB decision.
        alias_match: MediaAliasMatch | None = None
        try:
            alias_match = await self.media_alias_service.lookup(
                file_path=file_path,
                parsed_title=parsed.series_name,
            )
        except Exception as exc:
            logger.warning("读取媒体别名失败，继续常规匹配: %s", exc)
        if alias_match is not None:
            result.selected_id = alias_match.tmdb_id
            if alias_match.season is not None:
                parsed.season = alias_match.season
            if alias_match.episode is not None:
                parsed.episode = alias_match.episode

        # Step 2: Search TMDB using API
        search_step = ScrapeLogStep(name="搜索 TMDB", logs=[])
        search_step.logs.append(ScrapeLogEntry(message=f"搜索关键词: {parsed.series_name}"))
        scrape_logs.append(search_step)
        await notify_log_update()

        try:
            search_response = await self.tmdb_service.search_series_by_api(parsed.series_name)
            # 只保留成人内容
            adult_results = [r for r in search_response.results if r.adult]
            result.search_results = adult_results
            search_step.logs.append(ScrapeLogEntry(message=f"找到 {len(adult_results)} 个匹配结果"))
            await notify_log_update()
        except httpx.TimeoutException:
            search_step.logs.append(ScrapeLogEntry(message="TMDB 搜索超时", level=ScrapeLogLevel.ERROR))
            search_step.completed = False
            await notify_log_update()
            result.status = ScrapeStatus.SEARCH_FAILED
            result.message = "TMDB 搜索超时，请检查网络或 Cookie"
            result.scrape_logs = scrape_logs
            return result
        except httpx.RequestError as e:
            search_step.logs.append(ScrapeLogEntry(message=f"TMDB 搜索失败: {str(e)}", level=ScrapeLogLevel.ERROR))
            search_step.completed = False
            await notify_log_update()
            result.status = ScrapeStatus.SEARCH_FAILED
            result.message = f"TMDB 搜索失败: {str(e)}"
            result.scrape_logs = scrape_logs
            return result

        # AI is advisory by default. It may expand a difficult title search,
        # but it may only auto-select a candidate when its confidence policy
        # allows it; otherwise the existing manual-selection path remains.
        ai_requires_confirmation = False
        if alias_match is None:
            ai_config = await self.ai_provider.get_config()
            if ai_config.enabled:
                ai_step = ScrapeLogStep(
                    name="AI 强制识别" if ai_config.usage_mode == AIUsageMode.FORCE_USE else "AI 辅助识别",
                    logs=[],
                )
                scrape_logs.append(ai_step)
                try:
                    candidates = [
                        AICandidate(
                            id=item.id,
                            title=item.name,
                            original_title=item.original_name,
                            year=item.first_air_date.year if item.first_air_date else None,
                            overview=item.overview,
                        )
                        for item in adult_results
                    ]
                    evidence = {
                        "filename": path.name,
                        "parsed_title": parsed.series_name,
                        "parsed_season": parsed.season,
                        "parsed_episode": parsed.episode,
                        "parser_confidence": parsed.confidence,
                        "suffix": path.suffix.lower(),
                    }
                    ai_result = await self.ai_provider.recognize(
                        file_path=file_path,
                        evidence=evidence,
                        candidates=candidates,
                    )
                    ai_requires_confirmation = ai_result.needs_confirmation
                    if ai_result.selected_candidate_id is not None and not ai_result.needs_confirmation:
                        selected_id = str(ai_result.selected_candidate_id)
                        selected = next(
                            (item for item in adult_results if str(item.id) == selected_id),
                            None,
                        )
                        if selected is not None:
                            result.selected_id = selected.id
                            ai_step.logs.append(
                                ScrapeLogEntry(message=f"AI 高置信度选择 TMDB 候选: {selected.name}")
                            )
                    if not adult_results and ai_result.search_titles:
                        for title in build_search_title_variants(ai_result.search_titles[0]):
                            retry_response = await self.tmdb_service.search_series_by_api(title)
                            adult_results = [
                                item for item in retry_response.results if item.adult
                            ]
                            if adult_results:
                                result.search_results = adult_results
                                break
                    ai_step.logs.append(
                        ScrapeLogEntry(message=ai_result.reason or "AI 已返回识别建议")
                    )
                except AIProviderError as exc:
                    ai_requires_confirmation = ai_config.usage_mode == AIUsageMode.FORCE_USE
                    ai_step.completed = False
                    ai_step.logs.append(
                        ScrapeLogEntry(
                            message=f"AI 识别失败: {exc}",
                            level=ScrapeLogLevel.WARNING,
                        )
                    )
                except (httpx.RequestError, ValueError) as exc:
                    ai_requires_confirmation = ai_config.usage_mode == AIUsageMode.FORCE_USE
                    ai_step.completed = False
                    ai_step.logs.append(
                        ScrapeLogEntry(
                            message=f"AI 建议标题搜索失败: {exc}",
                            level=ScrapeLogLevel.WARNING,
                        )
                    )
                await notify_log_update()

        if not adult_results and alias_match is None:
            search_step.logs.append(ScrapeLogEntry(message="未找到匹配的成人剧集", level=ScrapeLogLevel.WARNING))
            search_step.completed = False
            await notify_log_update()
            result.status = ScrapeStatus.NO_MATCH
            result.message = f"未找到匹配的成人剧集: {parsed.series_name}"
            result.scrape_logs = scrape_logs
            return result

        # Step 3: Select match
        result.search_results = adult_results

        if result.selected_id is not None:
            pass
        elif request.auto_select and len(adult_results) == 1 and not ai_requires_confirmation:
            # 只有一个结果时自动选择
            selected = adult_results[0]
            result.selected_id = selected.id
        elif request.auto_select and len(adult_results) > 1:
            # 多个结果时需要用户选择，先获取每个结果的详情
            search_step.logs.append(ScrapeLogEntry(message="获取各剧集详情..."))
            await notify_log_update()
            enriched_results = await self._enrich_search_results(adult_results)
            result.search_results = enriched_results
            result.status = ScrapeStatus.NEED_SELECTION
            result.message = f"找到 {len(adult_results)} 个匹配结果，请手动选择"
            result.scrape_logs = scrape_logs
            return result
        else:
            # Return results for manual selection
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
                detail_step.logs.append(ScrapeLogEntry(message="无法获取剧集详情", level=ScrapeLogLevel.ERROR))
                detail_step.completed = False
                await notify_log_update()
                result.status = ScrapeStatus.API_FAILED
                result.message = f"无法获取剧集详情: ID {result.selected_id}"
                result.scrape_logs = scrape_logs
                return result
            result.series_info = series
            detail_step.logs.append(ScrapeLogEntry(message=f"剧集名称: {series.name}"))
            await notify_log_update()
        except ValueError as e:
            detail_step.logs.append(ScrapeLogEntry(message=str(e), level=ScrapeLogLevel.ERROR))
            detail_step.completed = False
            await notify_log_update()
            result.status = ScrapeStatus.API_FAILED
            result.message = str(e)
            result.scrape_logs = scrape_logs
            return result
        except httpx.TimeoutException:
            detail_step.logs.append(ScrapeLogEntry(message="TMDB API 请求超时", level=ScrapeLogLevel.ERROR))
            detail_step.completed = False
            await notify_log_update()
            result.status = ScrapeStatus.API_FAILED
            result.message = "TMDB API 请求超时"
            result.scrape_logs = scrape_logs
            return result
        except httpx.RequestError as e:
            detail_step.logs.append(ScrapeLogEntry(message=f"TMDB API 请求失败: {str(e)}", level=ScrapeLogLevel.ERROR))
            detail_step.completed = False
            await notify_log_update()
            result.status = ScrapeStatus.API_FAILED
            result.message = f"TMDB API 请求失败: {str(e)}"
            result.scrape_logs = scrape_logs
            return result

        # Step 4.5: Check if episode is missing
        if parsed.episode is None:
            # 如果剧集只有1集，自动选择
            total_episodes = series.number_of_episodes or 0
            if total_episodes == 1:
                parsed.episode = 1
                logger.info("剧集只有1集，自动选择 E01")
            else:
                # 多集需要手动选择，先获取季详情
                result.series_info = series
                season_num = parsed.season if parsed.season is not None else 1
                try:
                    season_detail = await self.tmdb_service.get_season_by_api(
                        result.selected_id, season_num
                    )
                    # 更新 series 中对应季的 episodes 信息
                    for i, s in enumerate(series.seasons):
                        if s.season_number == season_num:
                            series.seasons[i] = season_detail
                            break
                    result.series_info = series
                except Exception as e:
                    logger.warning(f"获取季度详情失败: {e}")

                result.status = ScrapeStatus.NEED_SEASON_EPISODE
                result.message = f"剧集共 {total_episodes} 集，请手动选择"
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
            logger.warning(f"获取季度详情失败: {e}")

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
                message=f"核验失败: {reason_text}", level=ScrapeLogLevel.WARNING,
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
        # 到这里 TMDB ID 与季/集才算定下来（之后只剩生成与搬运），落库后即使超时/失败，
        # 重试也能直接沿用这个匹配，不用用户重新搜一遍
        await notify_match_resolved(season_num, episode_num)

        # Step 5.5: Emby 冲突检查
        emby_step = ScrapeLogStep(name="Emby 冲突检查", logs=[])
        scrape_logs.append(emby_step)
        if request.skip_emby_check:
            conflict_result = ConflictCheckResult(conflict_type=ConflictType.NO_CONFLICT)
            emby_step.logs.append(ScrapeLogEntry(message="已按请求跳过 Emby 冲突检查"))
        else:
            try:
                conflict_result = await self._check_emby_conflict(
                    series_name=series.name,
                    tmdb_id=result.selected_id,
                    season=season_num,
                    episode=episode_num,
                )
            except Exception as e:
                logger.warning(f"Emby 冲突检查异常: {e}")
                conflict_result = ConflictCheckResult(conflict_type=ConflictType.NO_CONFLICT)

        if conflict_result.conflict_type == ConflictType.EPISODE_EXISTS:
            emby_step.logs.append(ScrapeLogEntry(
                message=conflict_result.message or "Emby 中已存在该集",
                level=ScrapeLogLevel.WARNING,
            ))
            emby_step.completed = False
            await notify_log_update()
            result.status = ScrapeStatus.EMBY_CONFLICT
            result.message = conflict_result.message
            result.emby_conflict = conflict_result
            result.scrape_logs = scrape_logs
            return result
        elif conflict_result.conflict_type == ConflictType.SERIES_EXISTS:
            emby_step.logs.append(ScrapeLogEntry(
                message=conflict_result.message or "Emby 中已存在该剧集",
                level=ScrapeLogLevel.SUCCESS,
            ))
        else:
            emby_step.logs.append(ScrapeLogEntry(message="无冲突"))
        await notify_log_update()

        # 更新实际使用的季/集号（经 _auto_correct_season 修正后的值）
        # 提前赋值，确保所有成功 return 路径（115→115 / 115→本地 / 纯本地）都带正确值
        result.parsed_season = season_num
        result.parsed_episode = episode_num

        # Step 6: Generate NFO
        nfo_step = ScrapeLogStep(name="生成 NFO", logs=[])
        scrape_logs.append(nfo_step)
        try:
            nfo_config = await self._get_effective_nfo_config(request.advanced_settings)
            nfo_content = self._generate_episode_nfo(
                series,
                season_num,
                episode_num,
                season_info,
                fields=nfo_config.get("episode"),
            )
            nfo_step.logs.append(ScrapeLogEntry(message="NFO 内容生成成功"))
            await notify_log_update()
        except Exception as e:
            nfo_step.logs.append(ScrapeLogEntry(message=f"NFO 生成失败: {str(e)}", level=ScrapeLogLevel.ERROR))
            nfo_step.completed = False
            await notify_log_update()
            result.status = ScrapeStatus.NFO_FAILED
            result.message = f"NFO 生成失败: {str(e)}"
            result.scrape_logs = scrape_logs
            return result

        # Step 7: Move file using RenameService
        return await self._move_and_finalize(
            request=request,
            result=result,
            scrape_logs=scrape_logs,
            notify_log_update=notify_log_update,
            series=series,
            season_num=season_num,
            episode_num=episode_num,
            season_info=season_info,
            nfo_content=nfo_content,
            notify_after_move_step=False,
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
        self._validate_storage_locators(request)
        file_path = request.file_path
        scrape_logs: list[ScrapeLogStep] = []

        async def notify_log_update():
            """通知日志更新。"""
            if on_log_update:
                await on_log_update(scrape_logs)

        if not self._is_provider_source(request.file_locator):
            try:
                validate_media_path(
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
                detail_step.logs.append(ScrapeLogEntry(message="无法获取剧集详情", level=ScrapeLogLevel.ERROR))
                detail_step.completed = False
                await notify_log_update()
                result.status = ScrapeStatus.API_FAILED
                result.message = f"无法获取剧集详情: ID {request.tmdb_id}"
                result.scrape_logs = scrape_logs
                return result
            result.series_info = series
            detail_step.logs.append(ScrapeLogEntry(message=f"剧集名称: {series.name}"))
            await notify_log_update()
        except ValueError as e:
            detail_step.logs.append(ScrapeLogEntry(message=str(e), level=ScrapeLogLevel.ERROR))
            detail_step.completed = False
            await notify_log_update()
            result.status = ScrapeStatus.API_FAILED
            result.message = str(e)
            result.scrape_logs = scrape_logs
            return result
        except (httpx.TimeoutException, httpx.RequestError) as e:
            detail_step.logs.append(ScrapeLogEntry(message=f"TMDB API 请求失败: {str(e)}", level=ScrapeLogLevel.ERROR))
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
            logger.warning(f"获取季度详情失败: {e}")

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
                message=f"核验失败: {reason_text}", level=ScrapeLogLevel.WARNING,
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

        # Step 2: 生成 NFO
        nfo_step = ScrapeLogStep(name="生成 NFO", logs=[])
        scrape_logs.append(nfo_step)
        await notify_log_update()
        try:
            nfo_config = await self._get_effective_nfo_config(request.advanced_settings)
            nfo_content = self._generate_episode_nfo(
                series,
                request.season,
                request.episode,
                season_info,
                fields=nfo_config.get("episode"),
            )
            nfo_step.logs.append(ScrapeLogEntry(message="NFO 内容生成成功"))
            await notify_log_update()
        except Exception as e:
            nfo_step.logs.append(ScrapeLogEntry(message=f"NFO 生成失败: {str(e)}", level=ScrapeLogLevel.ERROR))
            nfo_step.completed = False
            await notify_log_update()
            result.status = ScrapeStatus.NFO_FAILED
            result.message = f"NFO 生成失败: {str(e)}"
            result.scrape_logs = scrape_logs
            return result

        # Step 3: 移动文件
        return await self._move_and_finalize(
            request=request,
            result=result,
            scrape_logs=scrape_logs,
            notify_log_update=notify_log_update,
            series=series,
            season_num=request.season,
            episode_num=request.episode,
            season_info=season_info,
            nfo_content=nfo_content,
            notify_after_move_step=True,
        )


    async def reorganize_with_metadata(
        self,
        request: ScrapeByIdRequest,
        *,
        series: TMDBSeries,
        season_info: TMDBSeason | None = None,
        on_log_update: LogUpdateCallback | None = None,
    ) -> ScrapeResult:
        """用已存元数据重跑整理：不请求 TMDB，直接从元数据走 NFO → 移动/整理 → 图片/字幕。

        与 scrape_by_id 的差别只有一处：跳过 TMDB 取详情与季集核验（用户明确要求的语义）。
        换来的是可离线执行、结果与上次一致；代价是元数据不会因 TMDB 更新而变化——
        需要最新元数据时应该走重刮（retry）而不是这里。

        Args:
            request: 含源文件路径、季/集号、输出目录与整理模式的请求。
            series: 从历史记录重建的剧集元数据。
            season_info: 只含该集的季度信息，供 NFO 取集标题/简介/播出日期。
            on_log_update: 可选的日志回调。

        Returns:
            ScrapeResult，与正常刮削同一套状态码。
        """
        self._validate_storage_locators(request)
        file_path = request.file_path
        scrape_logs: list[ScrapeLogStep] = []

        async def notify_log_update():
            """通知日志更新。"""
            if on_log_update:
                await on_log_update(scrape_logs)

        meta_step = ScrapeLogStep(name="读取已存元数据", logs=[])
        meta_step.logs.append(
            ScrapeLogEntry(
                message=f"使用已存元数据: {series.name} S{request.season:02d}E{request.episode:02d}"
            )
        )
        scrape_logs.append(meta_step)
        await notify_log_update()

        if not self._is_provider_source(request.file_locator):
            try:
                validate_media_path(
                    file_path,
                    must_exist=True,
                    require_file=True,
                )
            except PathSecurityError as exc:
                meta_step.logs.append(
                    ScrapeLogEntry(message=str(exc), level=ScrapeLogLevel.ERROR)
                )
                meta_step.completed = False
                await notify_log_update()
                return ScrapeResult(
                    file_path=file_path,
                    status=ScrapeStatus.MOVE_FAILED,
                    message=str(exc),
                    scrape_logs=scrape_logs,
                )

        result = ScrapeResult(
            file_path=file_path,
            status=ScrapeStatus.SUCCESS,
            selected_id=request.tmdb_id,
            parsed_season=request.season,
            parsed_episode=request.episode,
            series_info=series,
        )

        # Step 1: 生成 NFO（同 scrape_by_id，只是数据来自已存元数据）
        nfo_step = ScrapeLogStep(name="生成 NFO", logs=[])
        scrape_logs.append(nfo_step)
        await notify_log_update()
        try:
            nfo_config = await self._get_effective_nfo_config(request.advanced_settings)
            nfo_content = self._generate_episode_nfo(
                series,
                request.season,
                request.episode,
                season_info,
                fields=nfo_config.get("episode"),
            )
            nfo_step.logs.append(ScrapeLogEntry(message="NFO 内容生成成功"))
            await notify_log_update()
        except Exception as e:
            nfo_step.logs.append(
                ScrapeLogEntry(message=f"NFO 生成失败: {str(e)}", level=ScrapeLogLevel.ERROR)
            )
            nfo_step.completed = False
            await notify_log_update()
            result.status = ScrapeStatus.NFO_FAILED
            result.message = f"NFO 生成失败: {str(e)}"
            result.scrape_logs = scrape_logs
            return result

        return await self._move_and_finalize(
            request=request,
            result=result,
            scrape_logs=scrape_logs,
            notify_log_update=notify_log_update,
            series=series,
            season_num=request.season,
            episode_num=request.episode,
            season_info=season_info,
            nfo_content=nfo_content,
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
