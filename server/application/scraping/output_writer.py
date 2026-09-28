"""刮削输出编排 - 存储输出、本地整理链路与元数据落盘。

从 service.py 拆出的协作者。所有协作调用经门面（ScraperService）分发：
保持实例级 monkeypatch 接缝（受测名字：_get_storage_provider /
_get_effective_nfo_config / _get_effective_download_config 等）与薄委托面不变。
"""

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Awaitable, Callable

from server.common.path_security import validate_media_path
from server.models.history import ScrapeLogEntry, ScrapeLogLevel, ScrapeLogStep
from server.models.manual_job import ManualJobAdvancedSettings
from server.models.organize import OrganizeMode
from server.models.rename import RenameRequest
from server.models.scraper import ScrapeResult, ScrapeStatus
from server.models.storage import StorageLocator, StorageProvider
from server.models.template import NamingTemplate

if TYPE_CHECKING:
    from server.application.scraping.service import ScraperService

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


class OutputWriter:
    """输出定位解析、115/本地整理执行与本地元数据落盘。"""

    def __init__(self, facade: "ScraperService") -> None:
        self._facade = facade

    async def resolve_p115_output_locator(self, locator: StorageLocator) -> StorageLocator:
        """输出目录缺少 file_id 时，按路径解析出真实的 115 目录 id。"""
        from server.domain.integration.p115_service import P115Service

        svc = P115Service(self._facade.config_service)
        directory_id = await svc.resolve_directory_id_by_path(locator.path)
        return StorageLocator(
            provider=StorageProvider.P115,
            path=locator.path,
            file_id=str(directory_id),
            is_dir=True,
        )

    def build_rename_request(
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
        naming_template: NamingTemplate | None = None,
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
            conflict_action=conflict_action,
            naming_template=naming_template,
        )

    async def finalize_storage_output(
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

        facade = self._facade
        mode = link_mode or OrganizeMode.MOVE
        naming_template = await facade._get_effective_naming_config(advanced_settings)
        rename_request = self.build_rename_request(
            source_path=source_path,
            title=title,
            season=season,
            episode=episode,
            output_dir=output_locator.path,
            link_mode=mode,
            year=year,
            naming_template=naming_template,
        )
        preview = facade.rename_service.preview_rename(rename_request)
        dest_path = Path(preview.dest_path)
        output_base = Path(output_locator.path)

        if output_locator.provider == StorageProvider.P115:
            provider = facade._get_storage_provider(StorageProvider.P115)

            # 输出目录必须有 file_id 才能创建子目录；缺失时按路径解析
            effective_output_locator = output_locator
            if not (output_locator.file_id or output_locator.parent_id):
                effective_output_locator = await self.resolve_p115_output_locator(output_locator)

            relative_dir = Path(preview.dest_folder).relative_to(output_base).as_posix()
            target_dir = await provider.ensure_directory(effective_output_locator, relative_dir)
            target_parent_id = target_dir.get("id")
            if not target_parent_id:
                raise ValueError("115 输出目录创建失败")

            if mode == OrganizeMode.COPY:
                await provider.copy(file_locator, dest_path.name, target_parent_id)
            elif mode == OrganizeMode.MOVE:
                await provider.rename(file_locator, dest_path.name, target_parent_id)
            else:
                raise ValueError(f"115 网盘暂不支持 {_get_mode_name(mode)} 输出")

            return StorageLocator(
                provider=StorageProvider.P115,
                path=str(dest_path).replace("\\", "/"),
                file_id=file_locator.file_id,
                parent_id=str(target_parent_id),
                is_dir=False,
            )

        if output_locator.provider == StorageProvider.LOCAL:
            provider = facade._get_storage_provider(StorageProvider.P115)
            with TemporaryDirectory(prefix="mhti-115-download-") as temp_dir:
                downloaded_path = await provider.download(file_locator, Path(temp_dir))
                local_request = self.build_rename_request(
                    source_path=str(downloaded_path),
                    title=title,
                    season=season,
                    episode=episode,
                    output_dir=output_locator.path,
                    link_mode=mode,
                    year=year,
                    naming_template=naming_template,
                )
                rename_result = facade.rename_service.execute_rename(
                    local_request,
                    allow_staged_source=True,
                )
                if not rename_result.success:
                    raise ValueError(rename_result.error or "本地整理失败")
            return StorageLocator(
                provider=StorageProvider.LOCAL,
                path=output_locator.path,
                is_dir=True,
            )

        raise ValueError(f"不支持的输出提供方: {output_locator.provider}")

    async def write_local_metadata_only(
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
        """在本地元数据目录写入 NFO/图片（视频已在 115，不落本地）。

        返回 (nfo_path, metadata_series_folder, metadata_season_folder)。
        仅在 metadata_dir 指向本地路径时执行；否则记录告警并返回空值。
        """
        if not metadata_dir and dest_path_override is None:
            move_step.logs.append(ScrapeLogEntry(
                message="未配置本地元数据目录，跳过 NFO/图片生成",
                level=ScrapeLogLevel.WARNING,
            ))
            await notify_log_update()
            return "", Path(), Path()
        if require_metadata_dir and not metadata_dir:
            move_step.logs.append(ScrapeLogEntry(
                message="未配置本地元数据目录，跳过 NFO/图片生成",
                level=ScrapeLogLevel.WARNING,
            ))
            await notify_log_update()
            return "", Path(), Path()

        facade = self._facade

        if dest_path_override is not None:
            dest_path = validate_media_path(str(dest_path_override))
            season_folder = dest_path.parent
            series_folder = season_folder.parent
            season_folder.mkdir(parents=True, exist_ok=True)
        else:
            # 通过预览得到剧集/季文件夹结构（不实际移动文件）
            # This preview does not move a video, but RenameService still requires
            # a namespace-safe absolute source path. Keep the synthetic source in
            # the selected output namespace so both local and 115 layouts work.
            preview_source = str(
                Path(output_dir_for_preview)
                / f".mhti-preview-{season:02d}e{episode:02d}.mp4"
            )
            preview_request = self.build_rename_request(
                source_path=preview_source,
                title=title,
                season=season,
                episode=episode,
                year=year,
                output_dir=output_dir_for_preview,
                link_mode=link_mode,
                naming_template=await facade._get_effective_naming_config(
                    advanced_settings
                ),
            )
            preview = facade.rename_service.preview_rename(preview_request)
            dest_path = Path(preview.dest_path)
            series_folder = Path(preview.dest_folder).parent
            season_folder = Path(preview.dest_folder)

        metadata_series_folder, metadata_season_folder = await self.resolve_metadata_folders(
            dest_file=dest_path,
            season_folder=season_folder,
            series_folder=series_folder,
            metadata_dir=metadata_dir,
        )

        # NFO
        nfo_config = await facade._get_effective_nfo_config(advanced_settings)
        nfo_path_str = ""
        if nfo_config["nfo_enabled"]:
            episode_fields = nfo_config.get("episode") or {}
            if episode_fields.get("enabled", True):
                nfo_path = validate_media_path(
                    str(metadata_season_folder / f"{dest_path.stem}.nfo")
                )
                nfo_path.write_text(nfo_content, encoding="utf-8")
                nfo_path_str = str(nfo_path)
                move_step.logs.append(ScrapeLogEntry(message=f"NFO 文件已写入: {nfo_path}"))

            if (nfo_config.get("tvshow") or {}).get("enabled", True):
                tvshow_nfo_path = validate_media_path(
                    str(metadata_series_folder / "tvshow.nfo")
                )
                if not tvshow_nfo_path.exists():
                    metadata_series_folder.mkdir(parents=True, exist_ok=True)
                    tvshow_nfo_data = facade.nfo_service.tvshow_from_tmdb(series)
                    tvshow_nfo_content = facade.nfo_service.generate_tvshow_nfo(
                        tvshow_nfo_data,
                        fields=nfo_config.get("tvshow"),
                    )
                    tvshow_nfo_path.write_text(tvshow_nfo_content, encoding="utf-8")
                    move_step.logs.append(ScrapeLogEntry(message="tvshow.nfo 已生成"))

            if (nfo_config.get("season") or {}).get("enabled", True):
                season_nfo_path = validate_media_path(
                    str(metadata_season_folder / "season.nfo")
                )
                if not season_nfo_path.exists():
                    season_nfo_data = facade._get_season_nfo_data(series, season)
                    season_nfo_content = facade.nfo_service.generate_season_nfo(
                        season_nfo_data,
                        fields=nfo_config.get("season"),
                    )
                    season_nfo_path.write_text(season_nfo_content, encoding="utf-8")
                    move_step.logs.append(ScrapeLogEntry(message="season.nfo 已生成"))
        else:
            move_step.logs.append(ScrapeLogEntry(message="NFO 生成已跳过（配置禁用）"))
        await notify_log_update()

        # 图片
        image_config = await facade._get_effective_image_config(advanced_settings)
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
            await facade._download_series_images(
                series,
                str(metadata_series_folder),
                download_poster=bool(image_config["series_poster"]),
                download_fanart=bool(image_config["series_backdrop"]),
                image_config=image_config,
            )
            move_step.logs.append(ScrapeLogEntry(message="剧集图片处理完成"))
        if image_config.get("season_poster"):
            await facade._download_season_image(
                season_info,
                season,
                str(metadata_season_folder),
                image_config,
            )
        if image_config["episode_thumb"]:
            await facade._download_episode_image(
                season_info,
                season,
                episode,
                str(metadata_season_folder),
                dest_path.stem,
                image_config=image_config,
            )
            move_step.logs.append(ScrapeLogEntry(message="集封面图处理完成"))
        await notify_log_update()

        return nfo_path_str, metadata_series_folder, metadata_season_folder

    def resolve_move_input(
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
        effective_metadata_dir = metadata_locator.path if metadata_locator else metadata_dir
        effective_source = file_locator.path if file_locator else file_path
        return effective_source, effective_output_dir, effective_metadata_dir

    async def organize_local_output(
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
        """沿用原本地整理链路。"""
        move_step.logs.append(ScrapeLogEntry(message=f"源文件: {source_display_path}"))
        move_step.logs.append(ScrapeLogEntry(message=f"目标目录: {output_dir_display or '原目录'}"))
        move_step.logs.append(ScrapeLogEntry(message=f"整理模式: {mode_name}"))
        await notify_log_update()

        rename_result = self._facade.rename_service.execute_rename(rename_request)
        if not rename_result.success:
            if rename_result.error and "already exists" in rename_result.error:
                move_step.logs.append(ScrapeLogEntry(message=f"目标文件已存在: {rename_result.dest_path}", level=ScrapeLogLevel.WARNING))
                move_step.completed = False
                await notify_log_update()
                result.status = ScrapeStatus.FILE_CONFLICT
                result.message = f"目标文件已存在: {rename_result.dest_path}"
                result.dest_path = rename_result.dest_path
                raise FileExistsError(rename_result.dest_path)
            raise ValueError(rename_result.error or "整理失败")

        result.dest_path = rename_result.dest_path
        move_step.logs.append(ScrapeLogEntry(message=f"文件{mode_name}成功: {rename_result.dest_path}"))
        await notify_log_update()

        dest_file = Path(rename_result.dest_path)
        season_folder = dest_file.parent
        series_folder = season_folder.parent
        return dest_file, season_folder, series_folder

    async def resolve_metadata_folders(
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
            metadata_series_folder = validate_media_path(
                str(metadata_base / series_folder.name)
            )
            metadata_season_folder = validate_media_path(
                str(metadata_series_folder / season_folder.name)
            )
            metadata_season_folder.mkdir(parents=True, exist_ok=True)
            return metadata_series_folder, metadata_season_folder

        return series_folder, season_folder
