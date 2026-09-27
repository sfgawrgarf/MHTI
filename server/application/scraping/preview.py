"""Scrape preview service."""

from server.common.path_security import validate_media_path
from server.models.scraper import ScrapePreview
from server.domain.parsing.parser_service import ParserService
from server.domain.metadata.tmdb_service import TMDBService


class ScrapePreviewService:
    """Service for generating scrape previews."""

    def __init__(
        self,
        parser_service: ParserService,
        tmdb_service: TMDBService,
    ):
        self.parser_service = parser_service
        self.tmdb_service = tmdb_service

    async def preview(self, file_path: str) -> ScrapePreview:
        """
        Generate a preview of what will be scraped.

        Args:
            file_path: Path to the video file.

        Returns:
            ScrapePreview with parsed info and search results.
        """
        path = validate_media_path(
            file_path,
            must_exist=True,
            require_file=True,
        )

        # Parse filename
        parsed = await self.parser_service.parse(path.name)

        # Search TMDB
        search_results = []
        if parsed.series_name:
            response = await self.tmdb_service.search_series(
                parsed.series_name,
                year=parsed.year,
            )
            search_results = response.results

        return ScrapePreview(
            file_path=file_path,
            parsed_info=parsed,
            search_results=search_results,
        )
