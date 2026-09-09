"""TMDB service for API-based metadata retrieval."""

import asyncio
import unicodedata
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from time import monotonic

import httpx

from server.core.exceptions import (
    TMDBConnectionError,
    TMDBError,
    TMDBInvalidCredentialsError,
    TMDBNotConfiguredError,
    TMDBNotFoundError,
    TMDBRateLimitError,
    TMDBTimeoutError,
)
from server.models.config import ApiTokenStatus
from server.models.tmdb import (
    TMDBEpisode,
    TMDBSearchResponse,
    TMDBSearchResult,
    TMDBSeason,
    TMDBSeries,
)
from server.services.config_service import ConfigService

TMDB_BASE_URL = "https://www.themoviedb.org"
TMDB_API_BASE_URL = "https://api.themoviedb.org/3"
TMDB_IMAGE_BASE_URL = "https://image.tmdb.org/t/p"


class TMDBService:
    """Service for TMDB operations using API."""

    def __init__(self, config_service: ConfigService):
        """Initialize TMDB service with explicit dependency."""
        self.config_service = config_service

    async def _get_proxy_url(self) -> str | None:
        """Get proxy URL from config."""
        config = await self.config_service.get_proxy_config()
        return config.get_url()

    async def _get_language(self) -> str:
        """Get primary language from config."""
        config = await self.config_service.get_language_config()
        return config.primary

    async def _get_api_token(self) -> str | None:
        """Get stored API token."""
        return await self.config_service.get_api_token()

    async def _get_timeout(self) -> float:
        """Get timeout from SystemConfig."""
        config = await self.config_service.get_system_config()
        return float(config.task_timeout)

    def _is_bearer_token(self, token: str) -> bool:
        """Check if token is a Bearer token (JWT format) or API Key."""
        return token.startswith("eyJ")

    def _format_proxy_error(self, error: Exception) -> str:
        """Format proxy-related runtime errors for user-facing responses."""
        message = str(error)
        if isinstance(error, ImportError) and "socksio" in message:
            return "SOCKS5 代理缺少运行依赖，请安装 httpx[socks]"
        return message

    @staticmethod
    def _retry_after(response: httpx.Response) -> float | None:
        """Read Retry-After seconds or an HTTP date without trusting the header."""
        value = response.headers.get("Retry-After")
        if not isinstance(value, str):
            return None
        try:
            if value.strip().isdigit():
                return float(int(value.strip()))
            retry_at = parsedate_to_datetime(value)
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
            return max(0.0, (retry_at - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return None

    @classmethod
    def _check_api_response(
        cls, response: httpx.Response, *, allow_not_found: bool = False
    ) -> None:
        """Keep upstream failures separate from valid empty/absent metadata."""
        status = response.status_code
        if status == 200 or (allow_not_found and status == 404):
            return
        if status == 401:
            # Keep this a gateway error, not an application login failure (401).
            raise TMDBInvalidCredentialsError("API Token")
        if status == 404:
            raise TMDBNotFoundError("接口")
        if status == 429:
            raise TMDBRateLimitError(cls._retry_after(response))
        message = (
            "TMDB 拒绝访问，请检查 API Token 权限"
            if status == 403
            else f"TMDB 服务请求失败 (HTTP {status})"
        )
        # Do not echo response bodies or URLs, which can contain credentials.
        raise TMDBError(message, details={"upstream_status": status})

    async def _make_api_request(
        self,
        endpoint: str,
        params: dict | None = None,
    ) -> httpx.Response:
        """
        Make HTTP request to TMDB API with authentication.

        Raises:
            TMDBNotConfiguredError: API Token 未配置
            TMDBTimeoutError: 请求超时
            TMDBConnectionError: 连接失败
        """
        token = await self._get_api_token()
        if not token:
            raise TMDBNotConfiguredError("API Token")

        proxy_url = await self._get_proxy_url()
        timeout = await self._get_timeout()
        url = f"{TMDB_API_BASE_URL}{endpoint}"

        config = await self.config_service.get_system_config()
        retries = max(0, min(config.retry_count, 3))
        headers = {"Accept": "application/json"}
        api_params = dict(params or {})
        if self._is_bearer_token(token):
            headers["Authorization"] = f"Bearer {token}"
        else:
            api_params["api_key"] = token

        deadline = monotonic() + timeout
        try:
            # Bound the entire request, including backoff, not each attempt alone.
            async with asyncio.timeout(timeout):
                async with httpx.AsyncClient(timeout=timeout, proxy=proxy_url) as client:
                    for attempt in range(retries + 1):
                        retry_after = None
                        try:
                            response = await client.get(url, headers=headers, params=api_params)
                            self._check_api_response(response, allow_not_found=True)
                            return response
                        except httpx.TimeoutException:
                            error = TMDBTimeoutError(endpoint)
                        except httpx.RequestError:
                            error = TMDBConnectionError()
                        except TMDBError as exc:
                            if response.status_code not in (429, 500, 502, 503, 504):
                                raise
                            error = exc
                            retry_after = self._retry_after(response)

                        delay = max(0.5 * (2 ** attempt), retry_after or 0.0)
                        # Never retry earlier than a long server-requested delay.
                        if attempt == retries or delay > 8.0 or delay >= deadline - monotonic():
                            raise error
                        await asyncio.sleep(delay)
        except TimeoutError:
            raise TMDBTimeoutError(endpoint) from None

    async def test_proxy(self, proxy_url: str | None = None) -> tuple[bool, str, int | None]:
        """
        Test proxy connection to TMDB.

        Args:
            proxy_url: Optional proxy URL to test. If None, uses configured proxy.

        Returns:
            Tuple of (success, message, latency_ms).
        """
        import time

        if proxy_url is None:
            proxy_url = await self._get_proxy_url()

        try:
            start = time.time()
            timeout = await self._get_timeout()
            async with httpx.AsyncClient(timeout=timeout, proxy=proxy_url) as client:
                response = await client.get(
                    TMDB_BASE_URL,
                    headers={"User-Agent": "Mozilla/5.0"},
                    follow_redirects=True,
                )
            latency = int((time.time() - start) * 1000)

            if response.status_code == 200:
                return True, "连接成功", latency
            else:
                return False, f"HTTP 错误: {response.status_code}", latency

        except httpx.TimeoutException:
            return False, "连接超时", None
        except httpx.ProxyError as e:
            return False, f"代理错误: {str(e)}", None
        except httpx.RequestError as e:
            return False, f"连接错误: {str(e)}", None
        except ImportError as e:
            return False, f"测试失败: {self._format_proxy_error(e)}", None
        except Exception as e:
            return False, f"测试失败: {str(e)}", None

    # ========== Utility Methods ==========

    def get_image_url(self, path: str | None, size: str = "w500") -> str | None:
        """
        Get full image URL from TMDB path.

        Args:
            path: Image path from TMDB (e.g., "/abc123.jpg")
            size: Image size (w92, w154, w185, w342, w500, w780, original)

        Returns:
            Full image URL or None if path is empty.
        """
        if not path:
            return None
        return f"{TMDB_IMAGE_BASE_URL}/{size}{path}"

    def _parse_date(self, date_str: str | None) -> date | None:
        """Parse date string to date object."""
        if not date_str:
            return None
        try:
            return datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            return None

    # ========== API Token Methods ==========

    async def verify_api_token(self, token: str) -> tuple[bool, str | None]:
        """
        Verify API token by making a test request.

        Supports both API Key (v3) and Bearer Token (v4).

        Args:
            token: The API token to verify.

        Returns:
            Tuple of (is_valid, error_message).
        """
        try:
            proxy_url = await self._get_proxy_url()
            url = f"{TMDB_API_BASE_URL}/configuration"

            if self._is_bearer_token(token):
                headers = {
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json",
                }
                params = None
            else:
                headers = {"Accept": "application/json"}
                params = {"api_key": token}

            timeout = await self._get_timeout()
            async with httpx.AsyncClient(timeout=timeout, proxy=proxy_url) as client:
                response = await client.get(url, headers=headers, params=params)

                if response.status_code == 200:
                    return True, None
                elif response.status_code == 401:
                    try:
                        error_data = response.json()
                        status_message = error_data.get("status_message", "")
                        if status_message:
                            return False, f"API Token 验证失败: {status_message}"
                    except Exception:
                        pass
                    return False, "API Token 无效或已过期"
                else:
                    try:
                        error_data = response.json()
                        status_message = error_data.get("status_message", "")
                        if status_message:
                            return False, f"验证失败: {status_message}"
                    except Exception:
                        pass
                    return False, f"验证失败: HTTP {response.status_code}"

        except httpx.TimeoutException:
            return False, "连接超时 - 请检查网络或代理设置"
        except httpx.RequestError as e:
            return False, f"连接错误: {str(e)}"
        except ImportError as e:
            return False, self._format_proxy_error(e)
        except Exception as e:
            return False, f"验证失败: {str(e)}"

    async def save_and_verify_api_token(self, token: str) -> ApiTokenStatus:
        """
        Verify API token first, then save if valid.

        Args:
            token: The API token to save.

        Returns:
            ApiTokenStatus with verification results.
        """
        if not token or not token.strip():
            return ApiTokenStatus(
                is_configured=False,
                is_valid=False,
                error_message="API Token 不能为空",
            )

        # 先验证 token
        is_valid, error = await self.verify_api_token(token.strip())

        if not is_valid:
            # 验证失败，不保存
            return ApiTokenStatus(
                is_configured=False,
                is_valid=False,
                error_message=error,
            )

        # 验证成功，保存 token
        await self.config_service.save_api_token(token.strip())
        await self.config_service.set_api_token_verified(True)

        _, verified_at = await self.config_service.get_api_token_verification()

        return ApiTokenStatus(
            is_configured=True,
            is_valid=True,
            last_verified=verified_at,
        )

    async def get_api_token_status(self) -> ApiTokenStatus:
        """Get current API token configuration status."""
        return await self.config_service.get_api_token_status()

    async def delete_api_token(self) -> bool:
        """Delete the stored API token."""
        return await self.config_service.delete_api_token()

    # ========== API-based Methods ==========

    async def search_series_by_api(
        self,
        query: str,
        language: str | None = None,
    ) -> TMDBSearchResponse:
        """
        Search TV series using TMDB API.

        Args:
            query: Search query string
            language: Language for results

        Returns:
            TMDBSearchResponse with search results.
        """
        query = unicodedata.normalize("NFC", query).strip()

        if language is None:
            language = await self._get_language()

        try:
            response = await self._make_api_request(
                "/search/tv",
                params={"query": query, "language": language, "include_adult": "true"},
            )

            self._check_api_response(response)

            data = response.json()
            if not isinstance(data, dict) or not isinstance(data.get("results"), list):
                raise ValueError("Invalid search payload")
            results = []

            for item in data.get("results", [])[:20]:
                first_air_date = None
                if item.get("first_air_date"):
                    try:
                        first_air_date = date.fromisoformat(item["first_air_date"])
                    except ValueError:
                        pass

                results.append(
                    TMDBSearchResult(
                        id=item["id"],
                        name=item.get("name", ""),
                        original_name=item.get("original_name"),
                        first_air_date=first_air_date,
                        poster_path=item.get("poster_path"),
                        overview=item.get("overview"),
                        vote_average=item.get("vote_average"),
                        adult=item.get("adult", False),
                    )
                )

            return TMDBSearchResponse(
                query=query,
                total_results=data.get("total_results", len(results)),
                results=results,
            )

        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise TMDBError("TMDB 搜索返回的数据格式无效") from exc
        except (httpx.TimeoutException, httpx.RequestError):
            raise

    async def get_series_by_api(
        self,
        tmdb_id: int,
        language: str | None = None,
    ) -> TMDBSeries | None:
        """
        Get TV series details from TMDB API.

        Args:
            tmdb_id: TMDB series ID
            language: Language for metadata (uses config if not specified)

        Returns:
            TMDBSeries with full details, or None if not found.
        """
        if language is None:
            language = await self._get_language()

        try:
            response = await self._make_api_request(
                f"/tv/{tmdb_id}",
                params={"language": language},
            )

            if response.status_code == 404:
                return None
            self._check_api_response(response)

            data = response.json()
            if not isinstance(data, dict) or not isinstance(data.get("seasons"), list):
                raise ValueError("Invalid series payload")
            return self._parse_series_json(data)

        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise TMDBError("TMDB 剧集详情返回的数据格式无效") from exc
        except (httpx.TimeoutException, httpx.RequestError):
            raise

    def _parse_series_json(self, data: dict) -> TMDBSeries:
        """Parse series data from API JSON response."""
        genres = [g["name"] for g in data.get("genres", [])]

        seasons = []
        for s in data.get("seasons", []):
            seasons.append(
                TMDBSeason(
                    season_number=s.get("season_number", 0),
                    name=s.get("name", ""),
                    overview=s.get("overview"),
                    air_date=self._parse_date(s.get("air_date")),
                    poster_path=s.get("poster_path"),
                    episode_count=s.get("episode_count"),
                )
            )

        return TMDBSeries(
            id=data["id"],
            name=data.get("name", ""),
            original_name=data.get("original_name"),
            overview=data.get("overview"),
            first_air_date=self._parse_date(data.get("first_air_date")),
            vote_average=data.get("vote_average"),
            poster_path=data.get("poster_path"),
            backdrop_path=data.get("backdrop_path"),
            genres=genres,
            status=data.get("status"),
            number_of_seasons=data.get("number_of_seasons"),
            number_of_episodes=data.get("number_of_episodes"),
            seasons=seasons,
        )

    async def get_season_by_api(
        self,
        tmdb_id: int,
        season_number: int,
        language: str | None = None,
    ) -> TMDBSeason | None:
        """
        Get season details including episodes from TMDB API.

        Args:
            tmdb_id: TMDB series ID
            season_number: Season number
            language: Language for metadata (uses config if not specified)

        Returns:
            TMDBSeason with episodes, or None if not found.
        """
        if language is None:
            language = await self._get_language()

        try:
            response = await self._make_api_request(
                f"/tv/{tmdb_id}/season/{season_number}",
                params={"language": language},
            )

            if response.status_code == 404:
                return None
            self._check_api_response(response)

            data = response.json()
            if (
                not isinstance(data, dict)
                or "season_number" not in data
                or not isinstance(data.get("episodes"), list)
            ):
                raise ValueError("Invalid season payload")
            return self._parse_season_json(data)

        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise TMDBError("TMDB 季详情返回的数据格式无效") from exc
        except (httpx.TimeoutException, httpx.RequestError):
            raise

    def _parse_season_json(self, data: dict) -> TMDBSeason:
        """Parse season data from API JSON response."""
        episodes = []
        for ep in data.get("episodes", []):
            episodes.append(
                TMDBEpisode(
                    episode_number=ep.get("episode_number", 0),
                    name=ep.get("name", ""),
                    overview=ep.get("overview"),
                    air_date=self._parse_date(ep.get("air_date")),
                    vote_average=ep.get("vote_average"),
                    still_path=ep.get("still_path"),
                )
            )

        return TMDBSeason(
            season_number=data.get("season_number", 0),
            name=data.get("name", ""),
            overview=data.get("overview"),
            air_date=self._parse_date(data.get("air_date")),
            poster_path=data.get("poster_path"),
            episode_count=len(episodes),
            episodes=episodes,
        )

    async def get_series_with_episodes(
        self,
        tmdb_id: int,
        language: str | None = None,
        include_episodes: bool = True,
    ) -> TMDBSeries | None:
        """
        Get TV series details with full episode information.

        Args:
            tmdb_id: TMDB series ID
            language: Language for metadata
            include_episodes: Whether to fetch episode details for each season

        Returns:
            TMDBSeries with complete season/episode data, or None if not found.
        """
        series = await self.get_series_by_api(tmdb_id, language)

        if series is None:
            return None

        if not include_episodes or not series.seasons:
            return series

        updated_seasons = []
        for season in series.seasons:
            try:
                season_detail = await self.get_season_by_api(
                    tmdb_id, season.season_number, language
                )

                if season_detail and season_detail.episodes:
                    updated_seasons.append(season_detail)
                else:
                    updated_seasons.append(season)
            except (TMDBError, httpx.RequestError):
                raise
            except Exception:
                updated_seasons.append(season)

        series.seasons = updated_seasons
        return series
