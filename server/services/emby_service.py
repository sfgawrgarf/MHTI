"""Emby integration service."""

import logging
import time

import httpx

from server.core.log_security import safe_log_value
from server.models.emby import (
    ConflictCheckRequest,
    ConflictCheckResult,
    ConflictType,
    EmbyConfig,
    EmbyEpisodeMatch,
    EmbyLibrary,
    EmbySeriesMatch,
    EmbyStatus,
    EmbyTestResponse,
)
from server.services.config_service import ConfigService

logger = logging.getLogger(__name__)


class EmbyService:
    """Emby 媒体服务器集成服务"""

    def __init__(self, config_service: ConfigService):
        """Initialize with explicit dependency."""
        self.config_service = config_service

    def _get_client(self, config: EmbyConfig) -> httpx.AsyncClient:
        """获取 HTTP 客户端"""
        headers = {
            "X-Emby-Token": config.api_key,
            "Accept": "application/json",
        }
        return httpx.AsyncClient(
            base_url=config.server_url.rstrip("/"),
            headers=headers,
            timeout=config.timeout,
        )

    async def get_config(self) -> EmbyConfig:
        """获取 Emby 配置"""
        return await self.config_service.get_emby_config()

    async def save_config(self, config: EmbyConfig) -> None:
        """保存 Emby 配置"""
        await self.config_service.save_emby_config(config)

    async def get_status(self) -> EmbyStatus:
        """获取 Emby 连接状态"""
        config = await self.get_config()
        if not config.enabled or not config.server_url or not config.api_key:
            return EmbyStatus(is_configured=False)
        return EmbyStatus(is_configured=True)

    async def test_connection(self) -> EmbyTestResponse:
        """测试 Emby 连接（使用已保存的配置）"""
        config = await self.get_config()
        return await self.test_connection_with_config(config)

    async def test_connection_with_config(self, config: EmbyConfig) -> EmbyTestResponse:
        """测试 Emby 连接（使用传入的配置）"""
        if not config.server_url or not config.api_key:
            return EmbyTestResponse(
                success=False,
                message="请先配置服务器地址和 API 密钥",
            )

        start_time = time.time()
        try:
            async with self._get_client(config) as client:
                # 获取服务器信息
                resp = await client.get("/System/Info/Public")
                resp.raise_for_status()
                info = resp.json()

                # 获取媒体库列表
                libraries = await self._get_libraries(client, config.user_id)

                latency = int((time.time() - start_time) * 1000)
                return EmbyTestResponse(
                    success=True,
                    message="连接成功",
                    server_name=info.get("ServerName"),
                    server_version=info.get("Version"),
                    libraries=libraries,
                    latency_ms=latency,
                )
        except httpx.TimeoutException:
            return EmbyTestResponse(success=False, message="连接超时")
        except httpx.RequestError as e:
            return EmbyTestResponse(success=False, message=f"连接失败: {str(e)}")
        except Exception as e:
            return EmbyTestResponse(success=False, message=f"错误: {str(e)}")

    async def _get_libraries(
        self, client: httpx.AsyncClient, user_id: str = ""
    ) -> list[EmbyLibrary]:
        """获取媒体库列表"""
        try:
            if user_id:
                # 使用用户视图 API，返回数据包含 ChildCount
                resp = await client.get(f"/Users/{user_id}/Views")
                resp.raise_for_status()
                data = resp.json()

                libraries = []
                items = data.get("Items", [])
                for item in items:
                    lib_type = item.get("CollectionType", "unknown")
                    if lib_type in ("tvshows", "movies", "mixed"):
                        libraries.append(
                            EmbyLibrary(
                                id=item.get("Id", ""),
                                name=item.get("Name", ""),
                                type=lib_type,
                                item_count=item.get("ChildCount", 0),
                            )
                        )
                return libraries
            else:
                # 使用 VirtualFolders API（不返回 ChildCount），需要额外获取项目数
                resp = await client.get("/Library/VirtualFolders")
                resp.raise_for_status()
                data = resp.json()

                libraries = []
                items = data if isinstance(data, list) else data.get("Items", data)
                for item in items:
                    lib_type = item.get("CollectionType", "unknown")
                    if lib_type in ("tvshows", "movies", "mixed"):
                        lib_id = item.get("ItemId", "")
                        lib_name = item.get("Name", "")

                        # 获取该媒体库的项目数量
                        item_count = 0
                        if lib_id:
                            try:
                                count_resp = await client.get(
                                    "/Items",
                                    params={
                                        "ParentId": lib_id,
                                        "Recursive": "false",
                                        "Limit": "0",
                                    },
                                )
                                if count_resp.status_code == 200:
                                    count_data = count_resp.json()
                                    item_count = count_data.get("TotalRecordCount", 0)
                            except Exception as e:
                                logger.debug(f"获取媒体库 {lib_name} 项目数失败: {e}")

                        libraries.append(
                            EmbyLibrary(
                                id=lib_id,
                                name=lib_name,
                                type=lib_type,
                                item_count=item_count,
                            )
                        )
                return libraries
        except Exception as e:
            logger.warning(f"获取媒体库列表失败: {e}")
            return []

    async def check_conflict(self, request: ConflictCheckRequest) -> ConflictCheckResult:
        """检查 Emby 中是否存在冲突"""
        config = await self.get_config()

        if not config.enabled or not config.check_before_scrape:
            return ConflictCheckResult(conflict_type=ConflictType.NO_CONFLICT)

        if not config.server_url or not config.api_key:
            return ConflictCheckResult(
                conflict_type=ConflictType.CHECK_FAILED,
                message="Emby 冲突检查已启用，但服务器地址或 API 密钥未配置",
            )

        try:
            async with self._get_client(config) as client:
                # 1. 搜索剧集
                series = await self._search_series(
                    client, request.series_name, request.tmdb_id, config
                )

                if not series:
                    return ConflictCheckResult(conflict_type=ConflictType.NO_CONFLICT)

                # 2. 检查集是否存在
                episode = await self._check_episode(
                    client, series.id, request.season, request.episode
                )

                if episode:
                    return ConflictCheckResult(
                        conflict_type=ConflictType.EPISODE_EXISTS,
                        message=f"Emby 中已存在: {series.name} S{request.season:02d}E{request.episode:02d}",
                        existing_series=series,
                        existing_episode=episode,
                    )

                return ConflictCheckResult(
                    conflict_type=ConflictType.SERIES_EXISTS,
                    message=f"Emby 中已存在剧集: {series.name}，但该集不存在",
                    existing_series=series,
                )

        except Exception as e:
            logger.warning(f"Emby 冲突检查失败: {e}")
            return ConflictCheckResult(
                conflict_type=ConflictType.CHECK_FAILED,
                message=f"Emby 冲突检查失败: {str(e)}",
            )

    async def _search_series(
        self,
        client: httpx.AsyncClient,
        name: str,
        tmdb_id: int | None,
        config: EmbyConfig,
    ) -> EmbySeriesMatch | None:
        """在 Emby 中搜索剧集"""
        params: dict[str, str] = {
            "SearchTerm": name,
            "IncludeItemTypes": "Series",
            "Recursive": "true",
            "Limit": "20",
            "Fields": "ProviderIds,Path",  # 需要获取 ProviderIds
        }

        items: list[dict] = []
        if config.library_ids:
            # Emby only accepts one ParentId per request. Query every selected
            # library explicitly; a global search cannot prove library membership.
            seen_ids: set[str] = set()
            for library_id in dict.fromkeys(config.library_ids):
                library_params = {**params, "ParentId": library_id}
                resp = await client.get("/Items", params=library_params)
                resp.raise_for_status()
                for item in resp.json().get("Items", []):
                    item_id = str(item.get("Id") or "")
                    if item_id and item_id not in seen_ids:
                        seen_ids.add(item_id)
                        items.append(item)
        else:
            resp = await client.get("/Items", params=params)
            resp.raise_for_status()
            items = resp.json().get("Items", [])
        # name is converted to a bounded single-line value.
        logger.info("Emby 搜索 '%s' 找到 %d 个结果", safe_log_value(name), len(items))

        for item in items:
            provider_ids = item.get("ProviderIds", {})
            item_name = item.get("Name", "")

            # 优先匹配 TMDB ID
            if tmdb_id and provider_ids.get("Tmdb") == str(tmdb_id):
                logger.info(f"Emby 匹配到剧集 (TMDB ID): {item_name}")
                return EmbySeriesMatch(
                    id=item["Id"],
                    name=item_name,
                    year=item.get("ProductionYear"),
                    path=item.get("Path"),
                    tmdb_id=tmdb_id,
                )

        # 第二轮：名称精确匹配
        for item in items:
            provider_ids = item.get("ProviderIds", {})
            item_name = item.get("Name", "")

            if item_name.lower() == name.lower():
                tmdb_str = provider_ids.get("Tmdb")
                logger.info(f"Emby 匹配到剧集 (名称): {item_name}")
                return EmbySeriesMatch(
                    id=item["Id"],
                    name=item_name,
                    year=item.get("ProductionYear"),
                    path=item.get("Path"),
                    tmdb_id=int(tmdb_str) if tmdb_str else None,
                )

        # 第三轮：名称包含匹配
        for item in items:
            provider_ids = item.get("ProviderIds", {})
            item_name = item.get("Name", "")

            if name.lower() in item_name.lower() or item_name.lower() in name.lower():
                tmdb_str = provider_ids.get("Tmdb")
                logger.info(f"Emby 匹配到剧集 (模糊): {item_name}")
                return EmbySeriesMatch(
                    id=item["Id"],
                    name=item_name,
                    year=item.get("ProductionYear"),
                    path=item.get("Path"),
                    tmdb_id=int(tmdb_str) if tmdb_str else None,
                )

        # name is converted to a bounded single-line value.
        logger.info("Emby 未找到匹配的剧集: %s", safe_log_value(name))
        return None

    async def _check_episode(
        self,
        client: httpx.AsyncClient,
        series_id: str,
        season: int,
        episode: int,
    ) -> EmbyEpisodeMatch | None:
        """检查特定集是否存在"""
        params = {
            "ParentId": series_id,
            "IncludeItemTypes": "Episode",
            "Recursive": "true",
            "Fields": "Path",
        }
        resp = await client.get("/Items", params=params)
        resp.raise_for_status()
        data = resp.json()

        items = data.get("Items", [])
        # series_id is converted to a bounded single-line value.
        logger.info(
            "Emby 剧集 %s 共有 %d 集",
            safe_log_value(series_id),
            len(items),
        )

        for item in items:
            item_season = item.get("ParentIndexNumber")
            item_episode = item.get("IndexNumber")
            if item_season == season and item_episode == episode:
                # Episode identifiers are converted to bounded single-line values.
                logger.info(
                    "Emby 找到匹配的集: S%sE%s",
                    safe_log_value(f"{season:02d}"),
                    safe_log_value(f"{episode:02d}"),
                )
                return EmbyEpisodeMatch(
                    id=item["Id"],
                    name=item["Name"],
                    season=season,
                    episode=episode,
                    path=item.get("Path"),
                    series_id=series_id,
                    series_name=item.get("SeriesName", ""),
                )

        # Episode identifiers are converted to bounded single-line values.
        logger.info(
            "Emby 未找到集: S%sE%s",
            safe_log_value(f"{season:02d}"),
            safe_log_value(f"{episode:02d}"),
        )
        return None
