"""Regression tests for CodeQL security boundaries."""

from pathlib import Path

import pytest

from server.core.log_security import safe_log_value
from server.core.path_security import (
    PathSecurityError,
    validate_image_url,
    validate_media_directory,
    validate_media_path,
)
from server.services.media_identity_service import MediaIdentityService
from server.services.tmdb_service import TMDBService


def test_log_values_cannot_create_additional_records() -> None:
    value = safe_log_value("first\r\nforged\tentry\u2028next")

    assert "\r" not in value
    assert "\n" not in value
    assert "\t" not in value
    assert "\u2028" not in value
    assert value == "first\\r\\nforged\\x09entry\\x2028next"


def test_log_values_are_bounded() -> None:
    value = safe_log_value("x" * 600, max_length=32)

    assert value == f"{'x' * 32}...[truncated]"


@pytest.mark.parametrize(
    "url",
    [
        "https://image.tmdb.org:444/t/p/w500/poster.jpg",
        "https://user@image.tmdb.org/t/p/w500/poster.jpg",
        "https://image.tmdb.org/t/p/w500/poster.jpg#fragment",
        "https://image.tmdb.org/t/p/w500/poster.jpg\nhttps://127.0.0.1",
    ],
)
def test_image_url_rejects_ambiguous_authorities(url: str) -> None:
    with pytest.raises(PathSecurityError):
        validate_image_url(url)


def test_image_url_is_canonicalized(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MHTI_ALLOWED_IMAGE_HOSTS", "image.tmdb.org")

    assert validate_image_url(
        "https://IMAGE.TMDB.ORG.:443/t/p/w500/poster.jpg?x=1"
    ) == "https://image.tmdb.org/t/p/w500/poster.jpg?x=1"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "endpoint",
    [
        "//127.0.0.1/admin",
        "/tv/1?redirect=https://127.0.0.1",
        "/tv/../admin",
        "/search/person",
        "/tv/-1",
    ],
)
async def test_tmdb_request_rejects_non_allowlisted_routes(endpoint: str) -> None:
    service = TMDBService(config_service=None)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="Invalid TMDB API endpoint"):
        await service._make_api_request(endpoint)


def test_fingerprint_does_not_read_outside_media_roots(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("first", encoding="utf-8")
    monkeypatch.setenv("MHTI_ALLOWED_MEDIA_ROOTS", str(allowed))

    first = MediaIdentityService.fingerprint(str(outside))
    outside.write_text("changed", encoding="utf-8")

    assert MediaIdentityService.fingerprint(str(outside)) == first


def test_media_directory_rejects_regular_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    media_file = tmp_path / "episode.mkv"
    media_file.write_bytes(b"video")
    monkeypatch.setenv("MHTI_ALLOWED_MEDIA_ROOTS", str(tmp_path))

    with pytest.raises(PathSecurityError, match="路径不是目录"):
        validate_media_directory(str(media_file))


@pytest.mark.parametrize("name", ["existing.txt", "missing.txt"])
def test_outside_media_paths_are_rejected_before_existence_is_disclosed(
    name: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    (outside / "existing.txt").write_text("secret", encoding="utf-8")
    monkeypatch.setenv("MHTI_ALLOWED_MEDIA_ROOTS", str(allowed))

    with pytest.raises(PathSecurityError) as exc_info:
        validate_media_path(str(outside / name), must_exist=True)

    assert str(exc_info.value).startswith("路径不在允许的媒体目录中:")
