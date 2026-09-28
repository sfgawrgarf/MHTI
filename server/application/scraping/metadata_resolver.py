"""Scraper 元数据处理 - NFO 生成与搜索结果处理。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING

from server.models.nfo import EpisodeNFO, SeasonNFO
from server.models.tmdb import TMDBSearchResult, TMDBSeason, TMDBSeries

if TYPE_CHECKING:
    from server.domain.artifacts.nfo_service import NFOService
    from server.domain.metadata.tmdb_service import TMDBService

logger = logging.getLogger(__name__)


class ScraperMetadataResolver:
    """元数据处理，提供 NFO 生成和搜索结果处理方法。"""

    def __init__(self, tmdb_service: TMDBService, nfo_service: NFOService) -> None:
        self.tmdb_service = tmdb_service
        self.nfo_service = nfo_service

    async def enrich_search_results(
        self,
        results: list[TMDBSearchResult],
    ) -> list[TMDBSearchResult]:
        """为搜索结果添加详情信息（季数、集数）。

        Args:
            results: 搜索结果列表。

        Returns:
            添加了详情信息的搜索结果列表。
        """
        async def fetch_details(result: TMDBSearchResult) -> TMDBSearchResult:
            try:
                series = await self.tmdb_service.get_series_by_api(result.id)
                if series:
                    result.number_of_seasons = series.number_of_seasons
                    result.number_of_episodes = series.number_of_episodes
            except Exception as e:
                logger.warning(f"获取剧集 {result.id} 详情失败: {e}")
            return result

        # 并行获取所有结果的详情
        enriched = await asyncio.gather(*[fetch_details(r) for r in results])
        return list(enriched)

    def select_best_match(
        self,
        results: list[TMDBSearchResult],
        query: str,
    ) -> TMDBSearchResult:
        """从搜索结果中选择最佳匹配。

        Args:
            results: 搜索结果列表。
            query: 原始搜索关键词。

        Returns:
            最佳匹配的搜索结果。

        Raises:
            ValueError: 结果列表为空。
        """
        if not results:
            raise ValueError("No results to select from")

        query_lower = query.lower().strip()

        # 精确名称匹配
        for r in results:
            if r.name.lower() == query_lower:
                return r
            if r.original_name and r.original_name.lower() == query_lower:
                return r

        # 部分匹配，优先选择评分高的
        best = results[0]
        for r in results[1:]:
            # 优先选择名称包含查询词的结果
            if query_lower in r.name.lower():
                if r.vote_average and (not best.vote_average or r.vote_average > best.vote_average):
                    best = r

        return best

    def generate_episode_nfo(
        self,
        series: TMDBSeries,
        season_num: int,
        episode_num: int,
        season_info: TMDBSeason | None = None,
        fields: Mapping[str, bool] | None = None,
    ) -> str:
        """生成剧集 NFO 内容。

        Args:
            series: TMDB 剧集信息。
            season_num: 季号。
            episode_num: 集号。
            season_info: 可选的季度详情（包含更准确的剧集信息）。

        Returns:
            NFO XML 内容字符串。
        """
        # 优先从 season_info 获取（包含完整剧集信息）
        episode_title = f"Episode {episode_num}"
        episode_plot = None
        episode_aired = None
        episode_rating = None

        if season_info and season_info.episodes:
            for ep in season_info.episodes:
                if ep.episode_number == episode_num:
                    episode_title = ep.name
                    episode_plot = ep.overview
                    episode_aired = ep.air_date
                    episode_rating = ep.vote_average
                    logger.info(f"从季度详情获取剧集信息: {episode_title}")
                    break
        else:
            # 回退到 series.seasons（可能没有 episodes 详情）
            for season in series.seasons:
                if season.season_number == season_num and season.episodes:
                    for ep in season.episodes:
                        if ep.episode_number == episode_num:
                            episode_title = ep.name
                            episode_plot = ep.overview
                            episode_aired = ep.air_date
                            episode_rating = ep.vote_average
                            break

        nfo_data = EpisodeNFO(
            title=episode_title,
            season=season_num,
            episode=episode_num,
            plot=episode_plot,
            aired=episode_aired,
            rating=episode_rating,
        )

        return self.nfo_service.generate_episode_nfo(nfo_data, fields=fields)

    def get_season_nfo_data(self, series: TMDBSeries, season_num: int) -> SeasonNFO:
        """从剧集信息中获取季度 NFO 数据。

        Args:
            series: TMDB 剧集信息。
            season_num: 季号。

        Returns:
            SeasonNFO 数据对象。
        """
        # 从 series.seasons 中查找对应季度信息
        season_title = f"季 {season_num}"
        season_plot = None
        season_premiered = None

        for season in series.seasons:
            if season.season_number == season_num:
                season_title = season.name or f"季 {season_num}"
                season_plot = season.overview
                season_premiered = season.air_date
                break

        return SeasonNFO(
            season_number=season_num,
            title=season_title,
            plot=season_plot,
            premiered=season_premiered,
        )
