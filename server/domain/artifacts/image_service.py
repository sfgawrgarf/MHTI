"""Image download service with retry and concurrency support."""

import asyncio
import os
import stat
import tempfile
from pathlib import Path

import httpx

from server.models.image import (
    BatchDownloadResponse,
    ImageDownloadRequest,
    ImageDownloadResult,
    ImageSize,
)
from server.common.path_security import (
    PathSecurityError,
    validate_image_url,
    validate_media_path,
)
from server.domain.system.config_service import ConfigService

RETRY_DELAYS = [1, 2, 4]  # Exponential backoff

TMDB_IMAGE_BASE_URL = "https://image.tmdb.org/t/p"

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
MAX_IMAGE_BYTES = 20 * 1024 * 1024


class ImageService:
    """Service for downloading images with retry and concurrency support."""

    def __init__(self, config_service: ConfigService):
        """
        Initialize image service.

        Args:
            config_service: ConfigService for reading system config.
        """
        self.config_service = config_service
        self._headers = {
            "User-Agent": DEFAULT_USER_AGENT,
        }

    async def _get_system_config(self):
        """Get system config for timeout, retry, concurrency settings."""
        return await self.config_service.get_system_config()

    async def _get_proxy_url(self) -> str | None:
        """Get proxy URL from config."""
        config = await self.config_service.get_proxy_config()
        return config.get_url()

    def get_full_image_url(
        self,
        path: str | None,
        size: ImageSize = ImageSize.W500,
    ) -> str | None:
        """
        Get full TMDB image URL from path.

        Args:
            path: Image path from TMDB (e.g., "/abc123.jpg")
            size: Image size.

        Returns:
            Full image URL or None if path is empty.
        """
        if not path:
            return None
        return f"{TMDB_IMAGE_BASE_URL}/{size.value}{path}"

    async def download_image(
        self,
        url: str,
        save_path: str,
        filename: str,
    ) -> ImageDownloadResult:
        """
        Download a single image with retry support.

        Args:
            url: Image URL to download.
            save_path: Directory to save the image.
            filename: Filename for the saved image.

        Returns:
            ImageDownloadResult with success status.
        """
        requested_path = Path(save_path) / filename
        try:
            safe_url = validate_image_url(url)
            full_path = validate_media_path(str(requested_path))
        except PathSecurityError as exc:
            return ImageDownloadResult(
                url=url,
                save_path=str(requested_path),
                success=False,
                error=str(exc),
            )
        last_error: str | None = None

        config = await self._get_system_config()
        timeout = float(config.task_timeout)
        # ``retry_count`` is the number of retries after the initial request.
        # Keep one real attempt even when the user sets it to zero.
        total_attempts = max(0, int(config.retry_count)) + 1
        proxy_url = await self._get_proxy_url()

        for attempt in range(total_attempts):
            temporary_path: Path | None = None
            try:
                async with httpx.AsyncClient(timeout=timeout, proxy=proxy_url) as client:
                    response = await client.get(safe_url, headers=self._headers)

                    if response.status_code == 404:
                        return ImageDownloadResult(
                            url=url,
                            save_path=str(full_path),
                            success=False,
                            error="Image not found (404)",
                        )

                    response.raise_for_status()

                    content = response.content
                    if len(content) > MAX_IMAGE_BYTES:
                        return ImageDownloadResult(
                            url=url,
                            save_path=str(full_path),
                            success=False,
                            error="Image exceeds 20 MB limit",
                        )

                    # Ensure directory exists
                    full_path.parent.mkdir(parents=True, exist_ok=True)

                    # Write beside the destination and publish atomically. This
                    # also prevents a failed download from leaving a partial
                    # image that later code treats as complete.
                    with tempfile.NamedTemporaryFile(
                        mode="wb",
                        dir=full_path.parent,
                        prefix=".mhti-image-",
                        suffix=".part",
                        delete=False,
                    ) as image_file:
                        temporary_path = Path(image_file.name)
                        image_file.write(content)

                    mode = (
                        stat.S_IMODE(full_path.stat().st_mode)
                        if full_path.exists()
                        else 0o644
                    )
                    temporary_path.chmod(mode)
                    os.replace(temporary_path, full_path)
                    temporary_path = None

                    return ImageDownloadResult(
                        url=url,
                        save_path=str(full_path),
                        success=True,
                    )

            except httpx.TimeoutException:
                last_error = "Download timeout"
            except httpx.HTTPStatusError as e:
                last_error = f"HTTP error: {e.response.status_code}"
            except httpx.RequestError as e:
                last_error = f"Connection error: {str(e)}"
            except OSError as e:
                # File system error - don't retry
                return ImageDownloadResult(
                    url=url,
                    save_path=str(full_path),
                    success=False,
                    error=f"File system error: {str(e)}",
                )
            finally:
                if temporary_path is not None:
                    temporary_path.unlink(missing_ok=True)

            # Wait before retry (if not last attempt)
            if attempt < total_attempts - 1:
                delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
                await asyncio.sleep(delay)

        return ImageDownloadResult(
            url=url,
            save_path=str(full_path),
            success=False,
            error=last_error,
        )

    async def download_batch(
        self,
        requests: list[ImageDownloadRequest],
        concurrency: int | None = None,
    ) -> BatchDownloadResponse:
        """
        Download multiple images concurrently.

        Args:
            requests: List of download requests.
            concurrency: Maximum number of concurrent downloads (uses config if None).

        Returns:
            BatchDownloadResponse with all results.
        """
        if not requests:
            return BatchDownloadResponse(
                total=0,
                success=0,
                failed=0,
                results=[],
            )

        # Use config value if not specified
        if concurrency is None:
            config = await self._get_system_config()
            concurrency = config.concurrent_downloads

        # Create semaphore to limit concurrency
        semaphore = asyncio.Semaphore(concurrency)

        async def download_with_semaphore(
            request: ImageDownloadRequest,
        ) -> ImageDownloadResult:
            async with semaphore:
                return await self.download_image(
                    url=request.url,
                    save_path=request.save_path,
                    filename=request.filename,
                )

        # Execute all downloads concurrently
        tasks = [download_with_semaphore(req) for req in requests]
        results = await asyncio.gather(*tasks)

        success_count = sum(1 for r in results if r.success)
        failed_count = len(results) - success_count

        return BatchDownloadResponse(
            total=len(results),
            success=success_count,
            failed=failed_count,
            results=list(results),
        )

    def generate_series_image_requests(
        self,
        save_path: str,
        poster_path: str | None = None,
        backdrop_path: str | None = None,
        size: ImageSize | None = None,
        *,
        logo_path: str | None = None,
        banner_path: str | None = None,
        extra_backdrop_paths: list[str] | None = None,
        poster_size: ImageSize = ImageSize.W500,
        backdrop_size: ImageSize = ImageSize.W780,
        extra_backdrop_count: int = 0,
    ) -> list[ImageDownloadRequest]:
        """
        Generate download requests for series images.

        Args:
            save_path: Directory to save images.
            poster_path: TMDB poster path.
            backdrop_path: TMDB backdrop path.
            poster_size: Poster image size.
            backdrop_size: Backdrop/banner image size.
            extra_backdrop_count: Maximum number of extra backdrops.

        Returns:
            List of ImageDownloadRequest objects.
        """
        if size is not None:
            poster_size = size
            backdrop_size = ImageSize.ORIGINAL if size == ImageSize.ORIGINAL else ImageSize.W780
        requests = []

        if poster_path:
            url = self.get_full_image_url(poster_path, poster_size)
            if url:
                requests.append(
                    ImageDownloadRequest(
                        url=url,
                        save_path=save_path,
                        filename="poster.jpg",
                    )
                )

        if backdrop_path:
            url = self.get_full_image_url(backdrop_path, backdrop_size)
            if url:
                requests.append(
                    ImageDownloadRequest(
                        url=url,
                        save_path=save_path,
                        filename="backdrop.jpg",
                    )
                )

        for path, filename in (
            (logo_path, "logo.png"),
            (banner_path, "banner.jpg"),
        ):
            if not path:
                continue
            url = self.get_full_image_url(
                path,
                poster_size if filename == "logo.png" else backdrop_size,
            )
            if url:
                requests.append(
                    ImageDownloadRequest(
                        url=url,
                        save_path=save_path,
                        filename=filename,
                    )
                )

        if extra_backdrop_paths and extra_backdrop_count > 0:
            extra_folder = str(Path(save_path) / "extrafanart")
            for index, path in enumerate(extra_backdrop_paths[:extra_backdrop_count], 1):
                url = self.get_full_image_url(path, backdrop_size)
                if url:
                    requests.append(
                        ImageDownloadRequest(
                            url=url,
                            save_path=extra_folder,
                            filename=f"{index:03d}.jpg",
                        )
                    )

        return requests

    def generate_season_image_request(
        self,
        save_path: str,
        season_number: int,
        poster_path: str | None,
        size: ImageSize = ImageSize.W500,
    ) -> ImageDownloadRequest | None:
        """
        Generate download request for season poster.

        Args:
            save_path: Directory to save image.
            season_number: Season number for filename.
            poster_path: TMDB poster path.
            size: Image size.

        Returns:
            ImageDownloadRequest or None if no poster.
        """
        if not poster_path:
            return None

        url = self.get_full_image_url(poster_path, size)
        if not url:
            return None

        return ImageDownloadRequest(
            url=url,
            save_path=save_path,
            filename=f"season{season_number:02d}-poster.jpg",
        )

    def generate_episode_image_request(
        self,
        save_path: str,
        season_number: int,
        episode_number: int,
        still_path: str | None,
        size: ImageSize = ImageSize.W500,
        filename: str | None = None,
    ) -> ImageDownloadRequest | None:
        """
        Generate download request for episode thumbnail.

        Args:
            save_path: Directory to save image (Season folder).
            season_number: Season number for filename.
            episode_number: Episode number for filename.
            still_path: TMDB still image path.
            size: Image size.

        Returns:
            ImageDownloadRequest or None if no still.
        """
        if not still_path:
            return None

        url = self.get_full_image_url(still_path, size)
        if not url:
            return None

        return ImageDownloadRequest(
            url=url,
            save_path=save_path,
            filename=filename or f"S{season_number:02d}E{episode_number:02d}.jpg",
        )
