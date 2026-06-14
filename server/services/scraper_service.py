"""Scraper service for orchestrating the scraping workflow.

该模块是刮削工作流的核心编排器，通过 Mixin 模式组织代码：
- ScraperConfigMixin: 配置管理
- ScraperMetadataMixin: 元数据处理（NFO、搜索结果）
- ScraperMediaMixin: 媒体文件处理（图片、字幕、Emby）
"""

import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import httpx

from server.models.emby import ConflictType
from server.models.history import LogLevel, ScrapeLogEntry, ScrapeLogStep
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
from server.models.tmdb import TMDBSeries
from server.services.config_service import ConfigService
from server.services.emby_service import EmbyService
from server.services.image_service import ImageService
from server.services.nfo_service import NFOService
from server.services.parser_service import ParserService
from server.services.rename_service import RenameService
from server.services.scraper_config import ScraperConfigMixin
from server.services.scraper_media import ScraperMediaMixin
from server.services.scraper_metadata import ScraperMetadataMixin
from server.services.subtitle_service import SubtitleService
from server.services.tmdb_service import TMDBService

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
        client = p115_module.P115Client(
            config.cookies,
            check_for_relogin=False,
            ensure_cookies=False,
            app=config.app or p115_service_module.DEFAULT_APP,
            console_qrcode=False,
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
        try:
            response = await client.fs_files(
                {"cid": parent_pid, "offset": 0, "limit": 100, "show_dir": 1},
                async_=True,
            )
        except Exception:
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

        # 1. 先改名（用源 file_id，原地改名）
        if target_name:
            try:
                await client.fs_rename((file_id, target_name), async_=True)
            except Exception as exc:
                raise ValueError(f"115 文件改名失败: {target_name}") from exc

        # 2. 再移动到目标目录
        try:
            resp = await client.fs_move(file_id, pid=target_parent_id, async_=True)
        except Exception as exc:
            raise ValueError(f"115 文件移动失败: {file_id} -> {target_parent_id}") from exc

        return resp

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

        # 1. 复制到目标目录（保持原名）
        try:
            resp = await client.fs_copy(file_id, pid=target_parent_id, async_=True)
        except Exception as exc:
            raise ValueError(f"115 文件复制失败: {file_id} -> {target_parent_id}") from exc

        # 2. fs_copy 不返回新文件 id，按源文件名在目标目录查找新副本的 id
        new_id = None
        if isinstance(resp, dict) and resp.get("file_id"):
            new_id = resp.get("file_id")
        if not new_id:
            new_id = await self._find_file_id_in_dir(client, target_parent_id, source_name)

        # 3. 改名为目标名
        if target_name and new_id:
            try:
                await client.fs_rename((new_id, target_name), async_=True)
            except Exception as exc:
                # 改名失败不影响复制结果，但记录警告
                import logging
                logging.getLogger(__name__).warning(
                    f"115 复制后改名失败 (new_id={new_id}, target={target_name}): {exc}"
                )

        return resp

    async def _find_file_id_in_dir(
        self,
        client: Any,
        parent_pid: str,
        name: str,
    ) -> str | None:
        """在父目录下按名字查找文件的 fid（fs_copy 后定位新副本用）。"""
        try:
            response = await client.fs_files(
                {"cid": parent_pid, "offset": 0, "limit": 100, "show_dir": 1},
                async_=True,
            )
        except Exception:
            return None
        rows = response.get("data", []) if isinstance(response, dict) else []
        for row in rows:
            if not isinstance(row, dict):
                continue
            row_name = row.get("n") or row.get("name") or ""
            # 文件条目用 fid
            fid = row.get("fid")
            if row_name == name and fid and "fid" in row:
                return str(fid)
        return None

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
        from server.services.p115_service import P115Service, VIRTUAL_115_ROOT_PATH
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
            provider = self._get_storage_provider(StorageProvider.P115)
            with TemporaryDirectory(prefix="mhti-115-download-") as temp_dir:
                downloaded_path = await provider.download(file_locator, Path(temp_dir))
                local_request = self._build_rename_request(
                    source_path=str(downloaded_path),
                    title=title,
                    season=season,
                    episode=episode,
                    output_dir=output_locator.path,
                    link_mode=mode,
                    year=year,
                )
                rename_result = self.rename_service.execute_rename(local_request)
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
        output_dir_for_preview: str,
        nfo_content: str,
        series,
        season_info,
        move_step: ScrapeLogStep,
        notify_log_update,
        link_mode: OrganizeMode | None,
    ) -> tuple[str, Path, Path]:
        """在本地元数据目录写入 NFO/图片（视频已在 115，不落本地）。

        返回 (nfo_path, metadata_series_folder, metadata_season_folder)。
        仅在 metadata_dir 指向本地路径时执行；否则记录告警并返回空值。
        """
        if not metadata_dir:
            move_step.logs.append(ScrapeLogEntry(
                message="未配置本地元数据目录，跳过 NFO/图片生成",
                level=LogLevel.WARNING,
            ))
            await notify_log_update()
            return "", Path(), Path()

        # 通过预览得到剧集/季文件夹结构（不实际移动文件）
        preview_request = self._build_rename_request(
            source_path=f"{title} S{season:02d}E{episode:02d}",
            title=title,
            season=season,
            episode=episode,
            year=year,
            output_dir=output_dir_for_preview,
            link_mode=link_mode,
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

        # NFO
        nfo_config = await self._get_effective_nfo_config(None)
        nfo_path_str = ""
        if nfo_config["nfo_enabled"]:
            nfo_path = metadata_season_folder / f"{dest_path.stem}.nfo"
            nfo_path.write_text(nfo_content, encoding="utf-8")
            nfo_path_str = str(nfo_path)
            move_step.logs.append(ScrapeLogEntry(message=f"NFO 文件已写入: {nfo_path}"))

            tvshow_nfo_path = metadata_series_folder / "tvshow.nfo"
            if not tvshow_nfo_path.exists():
                metadata_series_folder.mkdir(parents=True, exist_ok=True)
                tvshow_nfo_data = self.nfo_service.tvshow_from_tmdb(series)
                tvshow_nfo_content = self.nfo_service.generate_tvshow_nfo(tvshow_nfo_data)
                tvshow_nfo_path.write_text(tvshow_nfo_content, encoding="utf-8")
                move_step.logs.append(ScrapeLogEntry(message="tvshow.nfo 已生成"))

            season_nfo_path = metadata_season_folder / "season.nfo"
            if not season_nfo_path.exists():
                season_nfo_data = self._get_season_nfo_data(series, season)
                season_nfo_content = self.nfo_service.generate_season_nfo(season_nfo_data)
                season_nfo_path.write_text(season_nfo_content, encoding="utf-8")
                move_step.logs.append(ScrapeLogEntry(message="season.nfo 已生成"))
        else:
            move_step.logs.append(ScrapeLogEntry(message="NFO 生成已跳过（配置禁用）"))
        await notify_log_update()

        # 图片
        download_config = await self._get_effective_download_config(None)
        if download_config["download_poster"] or download_config["download_fanart"]:
            await self._download_series_images(
                series,
                str(metadata_series_folder),
                download_poster=download_config["download_poster"],
                download_fanart=download_config["download_fanart"],
            )
            move_step.logs.append(ScrapeLogEntry(message="剧集图片处理完成"))
        if download_config["download_thumb"]:
            await self._download_episode_image(
                season_info, season, episode, str(metadata_season_folder), dest_path.stem
            )
            move_step.logs.append(ScrapeLogEntry(message="集封面图处理完成"))
        await notify_log_update()

        return nfo_path_str, metadata_series_folder, metadata_season_folder

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
        effective_metadata_dir = metadata_locator.path if metadata_locator else metadata_dir
        effective_source = file_locator.path if file_locator else file_path
        return effective_source, effective_output_dir, effective_metadata_dir

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
        """沿用原本地整理链路。"""
        move_step.logs.append(ScrapeLogEntry(message=f"源文件: {source_display_path}"))
        move_step.logs.append(ScrapeLogEntry(message=f"目标目录: {output_dir_display or '原目录'}"))
        move_step.logs.append(ScrapeLogEntry(message=f"整理模式: {mode_name}"))
        await notify_log_update()

        rename_result = self.rename_service.execute_rename(rename_request)
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
        await notify_log_update()

        dest_file = Path(rename_result.dest_path)
        season_folder = dest_file.parent
        series_folder = season_folder.parent
        return dest_file, season_folder, series_folder

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
            metadata_base = Path(metadata_dir)
            metadata_series_folder = metadata_base / series_folder.name
            metadata_season_folder = metadata_series_folder / season_folder.name
            metadata_season_folder.mkdir(parents=True, exist_ok=True)
            return metadata_series_folder, metadata_season_folder

        return series_folder, season_folder

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
                search_response = await self.tmdb_service.search_series_by_api(parsed.series_name)
                preview.search_results = search_response.results
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
        file_path = request.file_path
        path = Path(file_path)
        scrape_logs: list[ScrapeLogStep] = []

        async def notify_log_update():
            """通知日志更新。"""
            if on_log_update:
                await on_log_update(scrape_logs)

        # Check file exists
        if not self._is_provider_source(request.file_locator) and not path.exists():
            return ScrapeResult(
                file_path=file_path,
                status=ScrapeStatus.MOVE_FAILED,
                message=f"文件不存在: {file_path}",
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

        parse_step.logs.append(ScrapeLogEntry(message=f"解析结果: {parsed.series_name} S{parsed.season or '?'}E{parsed.episode or '?'}"))
        scrape_logs.append(parse_step)
        await notify_log_update()

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
            search_step.logs.append(ScrapeLogEntry(message="TMDB 搜索超时", level=LogLevel.ERROR))
            search_step.completed = False
            await notify_log_update()
            result.status = ScrapeStatus.SEARCH_FAILED
            result.message = "TMDB 搜索超时，请检查网络或 Cookie"
            result.scrape_logs = scrape_logs
            return result
        except httpx.RequestError as e:
            search_step.logs.append(ScrapeLogEntry(message=f"TMDB 搜索失败: {str(e)}", level=LogLevel.ERROR))
            search_step.completed = False
            await notify_log_update()
            result.status = ScrapeStatus.SEARCH_FAILED
            result.message = f"TMDB 搜索失败: {str(e)}"
            result.scrape_logs = scrape_logs
            return result

        if not adult_results:
            search_step.logs.append(ScrapeLogEntry(message="未找到匹配的成人剧集", level=LogLevel.WARNING))
            search_step.completed = False
            await notify_log_update()
            result.status = ScrapeStatus.NO_MATCH
            result.message = f"未找到匹配的成人剧集: {parsed.series_name}"
            result.scrape_logs = scrape_logs
            return result

        # Step 3: Select match
        result.search_results = adult_results

        if request.auto_select and len(adult_results) == 1:
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
        except ValueError as e:
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

        # Step 5.5: Emby 冲突检查
        emby_step = ScrapeLogStep(name="Emby 冲突检查", logs=[])
        scrape_logs.append(emby_step)
        try:
            conflict_result = await self._check_emby_conflict(
                series_name=series.name,
                tmdb_id=result.selected_id,
                season=season_num,
                episode=episode_num,
            )
        except Exception as e:
            logger.warning(f"Emby 冲突检查异常: {e}")
            from server.models.emby import ConflictCheckResult
            conflict_result = ConflictCheckResult(conflict_type=ConflictType.NO_CONFLICT)

        if conflict_result.conflict_type == ConflictType.EPISODE_EXISTS:
            emby_step.logs.append(ScrapeLogEntry(
                message=conflict_result.message or "Emby 中已存在该集",
                level=LogLevel.WARNING,
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
                level=LogLevel.SUCCESS,
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
            nfo_content = self._generate_episode_nfo(series, season_num, episode_num, season_info)
            nfo_step.logs.append(ScrapeLogEntry(message="NFO 内容生成成功"))
            await notify_log_update()
        except Exception as e:
            nfo_step.logs.append(ScrapeLogEntry(message=f"NFO 生成失败: {str(e)}", level=LogLevel.ERROR))
            nfo_step.completed = False
            await notify_log_update()
            result.status = ScrapeStatus.NFO_FAILED
            result.message = f"NFO 生成失败: {str(e)}"
            result.scrape_logs = scrape_logs
            return result

        # Step 7: Move file using RenameService
        mode_name = _get_mode_name(request.link_mode)
        move_step = ScrapeLogStep(name=f"{mode_name}文件", logs=[])
        scrape_logs.append(move_step)
        try:
            year = series.first_air_date.year if series.first_air_date else None
            source_display_path, effective_output_dir, effective_metadata_dir = self._resolve_move_input(
                file_path=file_path,
                file_locator=request.file_locator,
                output_dir=request.output_dir,
                output_locator=request.output_locator,
                metadata_dir=request.metadata_dir,
                metadata_locator=request.metadata_locator,
            )

            should_process_subtitles = True

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
                    )
                    result.nfo_path = nfo_path_str or None
                    result.status = ScrapeStatus.SUCCESS
                    result.message = "刮削完成"
                    result.scrape_logs = scrape_logs
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
                nfo_path = metadata_season_folder / f"{dest_file.stem}.nfo"
                nfo_path.write_text(nfo_content, encoding="utf-8")
                result.nfo_path = str(nfo_path)
                move_step.logs.append(ScrapeLogEntry(message=f"NFO 文件已写入: {nfo_path}"))

                # 生成 tvshow.nfo（剧集信息）到剧集文件夹
                tvshow_nfo_path = metadata_series_folder / "tvshow.nfo"
                if not tvshow_nfo_path.exists():
                    metadata_series_folder.mkdir(parents=True, exist_ok=True)
                    tvshow_nfo_data = self.nfo_service.tvshow_from_tmdb(series)
                    tvshow_nfo_content = self.nfo_service.generate_tvshow_nfo(tvshow_nfo_data)
                    tvshow_nfo_path.write_text(tvshow_nfo_content, encoding="utf-8")
                    move_step.logs.append(ScrapeLogEntry(message="tvshow.nfo 已生成"))

                # 生成 season.nfo 到季度文件夹
                season_nfo_path = metadata_season_folder / "season.nfo"
                if not season_nfo_path.exists():
                    season_nfo_data = self._get_season_nfo_data(series, season_num)
                    season_nfo_content = self.nfo_service.generate_season_nfo(season_nfo_data)
                    season_nfo_path.write_text(season_nfo_content, encoding="utf-8")
                    move_step.logs.append(ScrapeLogEntry(message="season.nfo 已生成"))
            else:
                move_step.logs.append(ScrapeLogEntry(message="NFO 生成已跳过（配置禁用）"))

            await notify_log_update()

            # Step 8: Download images (based on config)
            image_step = ScrapeLogStep(name="下载图片", logs=[])
            scrape_logs.append(image_step)
            await notify_log_update()

            download_config = await self._get_effective_download_config(request.advanced_settings)

            # 下载剧集封面和背景图到元数据剧集文件夹
            if download_config["download_poster"] or download_config["download_fanart"]:
                await self._download_series_images(
                    series,
                    str(metadata_series_folder),
                    download_poster=download_config["download_poster"],
                    download_fanart=download_config["download_fanart"],
                )
                image_step.logs.append(ScrapeLogEntry(message="剧集图片处理完成"))
            else:
                image_step.logs.append(ScrapeLogEntry(message="剧集图片下载已跳过（配置禁用）"))
            await notify_log_update()

            # 下载集封面图到元数据季度文件夹
            if download_config["download_thumb"]:
                await self._download_episode_image(
                    season_info, season_num, episode_num, str(metadata_season_folder), dest_file.stem
                )
                image_step.logs.append(ScrapeLogEntry(message="集封面图处理完成"))
            else:
                image_step.logs.append(ScrapeLogEntry(message="集封面图下载已跳过（配置禁用）"))
            await notify_log_update()

            # 处理关联字幕文件
            if should_process_subtitles:
                self._process_subtitles(local_source_path, str(dest_file))

        except FileExistsError:
            result.scrape_logs = scrape_logs
            return result
        except Exception as e:
            move_step.logs.append(ScrapeLogEntry(message=f"文件{mode_name}失败: {str(e)}", level=LogLevel.ERROR))
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
        await notify_log_update()
        return result

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
        file_path = request.file_path
        path = Path(file_path)
        scrape_logs: list[ScrapeLogStep] = []

        async def notify_log_update():
            """通知日志更新。"""
            if on_log_update:
                await on_log_update(scrape_logs)

        if not self._is_provider_source(request.file_locator) and not path.exists():
            return ScrapeResult(
                file_path=file_path,
                status=ScrapeStatus.MOVE_FAILED,
                message=f"文件不存在: {file_path}",
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
        except ValueError as e:
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

        # Step 2: 生成 NFO
        nfo_step = ScrapeLogStep(name="生成 NFO", logs=[])
        scrape_logs.append(nfo_step)
        await notify_log_update()
        try:
            nfo_content = self._generate_episode_nfo(
                series, request.season, request.episode, season_info
            )
            nfo_step.logs.append(ScrapeLogEntry(message="NFO 内容生成成功"))
            await notify_log_update()
        except Exception as e:
            nfo_step.logs.append(ScrapeLogEntry(message=f"NFO 生成失败: {str(e)}", level=LogLevel.ERROR))
            nfo_step.completed = False
            await notify_log_update()
            result.status = ScrapeStatus.NFO_FAILED
            result.message = f"NFO 生成失败: {str(e)}"
            result.scrape_logs = scrape_logs
            return result

        # Step 3: 移动文件
        mode_name = _get_mode_name(request.link_mode)
        move_step = ScrapeLogStep(name=f"{mode_name}文件", logs=[])
        scrape_logs.append(move_step)
        await notify_log_update()
        try:
            year = series.first_air_date.year if series.first_air_date else None
            source_display_path, effective_output_dir, effective_metadata_dir = self._resolve_move_input(
                file_path=file_path,
                file_locator=request.file_locator,
                output_dir=request.output_dir,
                output_locator=request.output_locator,
                metadata_dir=request.metadata_dir,
                metadata_locator=request.metadata_locator,
            )
            should_process_subtitles = True

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
                        season=request.season,
                        episode=request.episode,
                        source_path=source_display_path,
                        year=year,
                    )
                    result.dest_path = dest_locator.path
                    move_step.logs.append(ScrapeLogEntry(message=f"文件{mode_name}成功: {dest_locator.path}"))
                    await notify_log_update()
                    move_step.logs.append(ScrapeLogEntry(message="115 网盘视频已输出，开始生成本地元数据"))
                    await notify_log_update()
                    # 115→115：视频在 115，NFO/图片/字幕留本地
                    nfo_path_str, _, _ = await self._write_local_metadata_only(
                        title=series.name,
                        season=request.season,
                        episode=request.episode,
                        year=year,
                        metadata_dir=effective_metadata_dir,
                        output_dir_for_preview=effective_output_dir,
                        nfo_content=nfo_content,
                        series=series,
                        season_info=season_info,
                        move_step=move_step,
                        notify_log_update=notify_log_update,
                        link_mode=request.link_mode,
                    )
                    result.nfo_path = nfo_path_str or None
                    result.status = ScrapeStatus.SUCCESS
                    result.message = "刮削完成"
                    result.scrape_logs = scrape_logs
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
                            season=request.season,
                            episode=request.episode,
                            year=year,
                            output_dir=effective_output_dir,
                            link_mode=request.link_mode,
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
                    season=request.season,
                    episode=request.episode,
                    year=year,
                    output_dir=effective_output_dir,
                    link_mode=request.link_mode,
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

            # Write episode NFO (if enabled)
            nfo_config = await self._get_effective_nfo_config(request.advanced_settings)
            if nfo_config["nfo_enabled"]:
                nfo_path = metadata_season_folder / f"{dest_file.stem}.nfo"
                nfo_path.write_text(nfo_content, encoding="utf-8")
                result.nfo_path = str(nfo_path)
                move_step.logs.append(ScrapeLogEntry(message=f"NFO 文件已写入: {nfo_path}"))

                # 生成 tvshow.nfo（剧集信息）到剧集文件夹
                tvshow_nfo_path = metadata_series_folder / "tvshow.nfo"
                if not tvshow_nfo_path.exists():
                    metadata_series_folder.mkdir(parents=True, exist_ok=True)
                    tvshow_nfo_data = self.nfo_service.tvshow_from_tmdb(series)
                    tvshow_nfo_content = self.nfo_service.generate_tvshow_nfo(tvshow_nfo_data)
                    tvshow_nfo_path.write_text(tvshow_nfo_content, encoding="utf-8")
                    move_step.logs.append(ScrapeLogEntry(message="tvshow.nfo 已生成"))

                # 生成 season.nfo 到季度文件夹
                season_nfo_path = metadata_season_folder / "season.nfo"
                if not season_nfo_path.exists():
                    season_nfo_data = self._get_season_nfo_data(series, request.season)
                    season_nfo_content = self.nfo_service.generate_season_nfo(season_nfo_data)
                    season_nfo_path.write_text(season_nfo_content, encoding="utf-8")
                    move_step.logs.append(ScrapeLogEntry(message="season.nfo 已生成"))
            else:
                move_step.logs.append(ScrapeLogEntry(message="NFO 生成已跳过（配置禁用）"))

            await notify_log_update()

            # Step 4: 下载图片 (based on config)
            image_step = ScrapeLogStep(name="下载图片", logs=[])
            scrape_logs.append(image_step)
            await notify_log_update()

            download_config = await self._get_effective_download_config(request.advanced_settings)

            # 下载剧集封面和背景图到元数据剧集文件夹
            if download_config["download_poster"] or download_config["download_fanart"]:
                await self._download_series_images(
                    series,
                    str(metadata_series_folder),
                    download_poster=download_config["download_poster"],
                    download_fanart=download_config["download_fanart"],
                )
                image_step.logs.append(ScrapeLogEntry(message="剧集图片处理完成"))
            else:
                image_step.logs.append(ScrapeLogEntry(message="剧集图片下载已跳过（配置禁用）"))
            await notify_log_update()

            # 下载集封面图到元数据季度文件夹
            if download_config["download_thumb"]:
                await self._download_episode_image(
                    season_info, request.season, request.episode, str(metadata_season_folder), dest_file.stem
                )
                image_step.logs.append(ScrapeLogEntry(message="集封面图处理完成"))
            else:
                image_step.logs.append(ScrapeLogEntry(message="集封面图下载已跳过（配置禁用）"))
            await notify_log_update()

            # 处理关联字幕文件
            if should_process_subtitles:
                self._process_subtitles(local_source_path, str(dest_file))

        except FileExistsError:
            result.scrape_logs = scrape_logs
            return result
        except Exception as e:
            move_step.logs.append(ScrapeLogEntry(message=f"文件{mode_name}失败: {str(e)}", level=LogLevel.ERROR))
            move_step.completed = False
            await notify_log_update()
            result.status = ScrapeStatus.MOVE_FAILED
            result.message = f"文件{mode_name}失败: {str(e)}"
            result.scrape_logs = scrape_logs
            return result

        # 设置集信息
        if season_info and season_info.episodes:
            for ep in season_info.episodes:
                if ep.episode_number == request.episode:
                    result.episode_info = ep
                    break

        result.status = ScrapeStatus.SUCCESS
        result.message = "刮削完成"
        result.scrape_logs = scrape_logs
        await notify_log_update()
        return result

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
