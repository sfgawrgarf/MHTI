"""Scraper 媒体文件处理 - 图片下载、字幕处理与 Emby 冲突检测。"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Mapping

from server.common.path_security import validate_media_path
from server.infrastructure.file_operations import publish_file
from server.infrastructure.log_security import safe_log_value
from server.models.organize import OrganizeMode
from server.application.file_io import check_file_cancelled
from server.models.emby import ConflictCheckRequest, ConflictCheckResult, ConflictType
from server.models.image import ImageSize
from server.models.tmdb import TMDBSeason, TMDBSeries

if TYPE_CHECKING:
    from server.domain.integration.emby_service import EmbyService
    from server.domain.artifacts.image_service import ImageService
    from server.domain.artifacts.subtitle_service import SubtitleService

logger = logging.getLogger(__name__)


def _coerce_image_size(value: object, default: ImageSize) -> ImageSize:
    """Convert persisted ImageQuality enum/string values safely."""
    raw_value = getattr(value, "value", value)
    try:
        return ImageSize(str(raw_value))
    except (TypeError, ValueError):
        return default


class ScraperMediaPipeline:
    """媒体文件处理，提供图片下载、字幕处理和 Emby 冲突检测方法。"""

    def __init__(
        self,
        image_service: ImageService,
        subtitle_service: SubtitleService,
        emby_service: EmbyService,
    ) -> None:
        self.image_service = image_service
        self.subtitle_service = subtitle_service
        self.emby_service = emby_service

    async def download_series_images(
        self,
        series: TMDBSeries,
        series_folder: str,
        download_poster: bool = True,
        download_fanart: bool = True,
        image_config: Mapping[str, object] | None = None,
    ) -> None:
        """下载剧集海报和背景图。

        Args:
            series: TMDBSeries 对象，包含图片路径。
            series_folder: 剧集文件夹路径。
            download_poster: 是否下载海报图。
            download_fanart: 是否下载背景图。
        """
        if self.image_service is None:
            return
        config = dict(image_config or {})
        config.setdefault("series_poster", download_poster)
        config.setdefault("series_backdrop", download_fanart)
        overwrite_existing = bool(config.get("overwrite_existing", False))

        poster_size = _coerce_image_size(config.get("poster_quality"), ImageSize.W500)
        backdrop_size = _coerce_image_size(
            config.get("backdrop_quality"), ImageSize.W780
        )
        requests = self.image_service.generate_series_image_requests(
            save_path=series_folder,
            poster_path=series.poster_path if config.get("series_poster") else None,
            backdrop_path=series.backdrop_path if config.get("series_backdrop") else None,
            logo_path=series.logo_path if config.get("series_logo") else None,
            banner_path=(series.banner_path or series.backdrop_path)
            if config.get("series_banner")
            else None,
            extra_backdrop_paths=(
                series.extra_backdrop_paths
                if config.get("extra_backdrops")
                else None
            ),
            poster_size=poster_size,
            backdrop_size=backdrop_size,
            extra_backdrop_count=max(
                0, int(config.get("extra_backdrops_count") or 0)
            ),
        )

        if not requests:
            logger.info("没有可下载的剧集图片")
            return

        filtered_requests = [
            req
            for req in requests
            if overwrite_existing or not (Path(req.save_path) / req.filename).exists()
        ]

        if not filtered_requests:
            logger.info("剧集图片已存在，跳过下载")
            return

        # 下载图片
        logger.info(f"开始下载剧集图片: {len(filtered_requests)} 个")
        result = await self.image_service.download_batch(filtered_requests)
        logger.info(f"图片下载完成: 成功 {result.success}, 失败 {result.failed}")

    async def download_season_image(
        self,
        season_info: TMDBSeason | None,
        season_num: int,
        season_folder: str,
        image_config: Mapping[str, object],
    ) -> None:
        """Download the configured season poster."""
        if self.image_service is None or not image_config.get("season_poster") or not season_info:
            return
        request = self.image_service.generate_season_image_request(
            save_path=season_folder,
            season_number=season_num,
            poster_path=season_info.poster_path,
            size=_coerce_image_size(
                image_config.get("poster_quality"), ImageSize.W500
            ),
        )
        if request is None:
            return
        target = Path(request.save_path) / request.filename
        if target.exists() and not image_config.get("overwrite_existing"):
            return
        result = await self.image_service.download_batch([request])
        if result.failed:
            logger.warning("季海报下载失败: %s", result.results[0].error)

    async def download_episode_image(
        self,
        season_info: TMDBSeason | None,
        season_num: int,
        episode_num: int,
        season_folder: str,
        video_stem: str,
        image_config: Mapping[str, object] | None = None,
    ) -> None:
        """下载集封面图，使用与视频文件相同的文件名。

        Args:
            season_info: TMDBSeason 对象，包含剧集信息。
            season_num: 季号。
            episode_num: 集号。
            season_folder: 季度文件夹路径。
            video_stem: 视频文件名（不含扩展名）。
        """
        if self.image_service is None:
            return
        if not season_info or not season_info.episodes:
            logger.info("没有季度信息，跳过集封面图下载")
            return

        # 查找当前集的 still_path
        still_path = None
        for ep in season_info.episodes:
            if ep.episode_number == episode_num:
                still_path = ep.still_path
                break

        if not still_path:
            logger.info("当前集没有封面图，跳过下载")
            return

        # 使用与视频文件相同的文件名
        config = dict(image_config or {})
        target_filename = f"{video_stem}.jpg"
        target_path = Path(season_folder) / target_filename
        if target_path.exists() and not config.get("overwrite_existing"):
            logger.info(f"集封面图已存在: {target_filename}")
            return

        request = self.image_service.generate_episode_image_request(
            save_path=season_folder,
            season_number=season_num,
            episode_number=episode_num,
            still_path=still_path,
            size=_coerce_image_size(config.get("thumb_quality"), ImageSize.W500),
            filename=target_filename,
        )
        if request is None:
            return

        # 下载图片
        logger.info(f"开始下载集封面图: {target_filename}")
        result = await self.image_service.download_image(
            url=request.url,
            save_path=request.save_path,
            filename=request.filename,
        )
        if result.success:
            logger.info(f"集封面图下载成功: {target_filename}")
        else:
            logger.warning(f"集封面图下载失败: {result.error}")

    def process_subtitles(
        self,
        source_video_path: str,
        dest_video_path: str,
        link_mode: OrganizeMode | None = None,
        conflict_action: str | None = None,
    ) -> list[str]:
        """查找并移动与视频关联的字幕文件。

        Args:
            source_video_path: 原视频文件路径。
            dest_video_path: 目标视频文件路径。

        Returns:
            已移动的字幕文件路径列表。
        """
        source_path = validate_media_path(source_video_path)
        dest_path = validate_media_path(dest_video_path)
        source_folder = source_path.parent
        dest_folder = dest_path.parent
        source_stem = source_path.stem
        dest_stem = dest_path.stem

        moved_subtitles = []

        # 扫描源文件夹中的字幕
        scan_result = self.subtitle_service.scan_subtitles(str(source_folder))
        if not scan_result.subtitles:
            logger.info("未找到关联字幕文件")
            return moved_subtitles

        # 查找匹配的字幕
        for sub in scan_result.subtitles:
            check_file_cancelled()
            if Path(sub.path).parent.resolve() != source_folder:
                continue
            sub_base = self.subtitle_service._get_base_name(sub.filename)
            if self.subtitle_service._names_match(source_stem, sub_base):
                language = f".{sub.language.value}" if sub.language else ""
                final_path = validate_media_path(str(dest_folder / f"{dest_stem}{language}{sub.extension}"))
                source_subtitle = validate_media_path(sub.path, must_exist=True, require_file=True)
                try:
                    publish_file(source_subtitle, final_path, link_mode,
                                 overwrite=conflict_action == "overwrite")
                    moved_subtitles.append(str(final_path))
                except OSError as exc:
                    logger.warning("字幕处理失败: %s", safe_log_value(exc))

        return moved_subtitles

    async def check_emby_conflict(
        self,
        series_name: str,
        tmdb_id: int | None,
        season: int,
        episode: int,
    ) -> ConflictCheckResult:
        """检查 Emby 媒体库冲突。

        Args:
            series_name: 剧集名称。
            tmdb_id: TMDB ID。
            season: 季号。
            episode: 集号。

        Returns:
            冲突检测结果。
        """
        config = await self.emby_service.get_config()

        if not config.enabled or not config.check_before_scrape:
            return ConflictCheckResult(conflict_type=ConflictType.NO_CONFLICT)

        return await self.emby_service.check_conflict(
            ConflictCheckRequest(
                series_name=series_name,
                tmdb_id=tmdb_id,
                season=season,
                episode=episode,
            )
        )
