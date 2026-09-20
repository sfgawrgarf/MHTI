"""Storage provider capability validation tests."""

import pytest

from server.models.manual_job import LinkMode, ManualJobCreate
from server.models.organize import OrganizeConfig, OrganizeMode
from server.models.scraper import ScrapeByIdRequest
from server.models.storage import StorageLocator, StorageProvider


def _p115_locator(path: str, *, is_dir: bool = True) -> StorageLocator:
    return StorageLocator(
        provider=StorageProvider.P115,
        path=path,
        file_id="folder-1" if is_dir else "file-1",
        is_dir=is_dir,
    )


def test_manual_job_requires_permission_for_p115_to_local() -> None:
    with pytest.raises(ValueError, match="允许下载到本地"):
        ManualJobCreate(
            scan_path="/115网盘/待整理",
            target_folder="/library",
            scan_locator=_p115_locator("/115网盘/待整理"),
            link_mode=LinkMode.COPY,
        )


def test_manual_job_normalizes_and_allows_approved_p115_to_local() -> None:
    request = ManualJobCreate(
        scan_path="/115网盘/待整理",
        target_folder="/library",
        scan_locator=_p115_locator("/115网盘/待整理"),
        allow_local_output=True,
        link_mode=LinkMode.COPY,
    )

    assert request.target_locator is not None
    assert request.target_locator.provider == StorageProvider.LOCAL


def test_manual_job_normalizes_legacy_p115_local_move_to_copy() -> None:
    request = ManualJobCreate(
        scan_path="/115网盘/待整理",
        target_folder="/library",
        scan_locator=_p115_locator("/115网盘/待整理"),
        allow_local_output=True,
        link_mode=LinkMode.MOVE,
    )

    assert request.link_mode == LinkMode.COPY


@pytest.mark.parametrize("mode", [LinkMode.HARDLINK, LinkMode.SYMLINK])
def test_manual_job_rejects_link_modes_for_p115_source(mode: LinkMode) -> None:
    with pytest.raises(ValueError, match="仅支持复制或移动"):
        ManualJobCreate(
            scan_path="/115网盘/待整理",
            target_folder="/115网盘/媒体库",
            scan_locator=_p115_locator("/115网盘/待整理"),
            target_locator=_p115_locator("/115网盘/媒体库"),
            link_mode=mode,
        )


def test_manual_job_rejects_local_to_p115_and_cloud_metadata() -> None:
    with pytest.raises(ValueError, match="本地文件输出到 115"):
        ManualJobCreate(
            scan_path="/incoming",
            target_folder="/115网盘/媒体库",
            target_locator=_p115_locator("/115网盘/媒体库"),
            link_mode=LinkMode.COPY,
        )

    with pytest.raises(ValueError, match="元数据目录仅支持本地"):
        ManualJobCreate(
            scan_path="/incoming",
            target_folder="/library",
            metadata_dir="/115网盘/元数据",
            metadata_locator=_p115_locator("/115网盘/元数据"),
        )


def test_direct_scrape_request_enforces_the_same_permission() -> None:
    file_locator = _p115_locator("/115网盘/待整理/S01E01.mkv", is_dir=False)
    with pytest.raises(ValueError, match="允许下载到本地"):
        ScrapeByIdRequest(
            file_path=file_locator.path,
            tmdb_id=1,
            season=1,
            episode=1,
            output_dir="/library",
            file_locator=file_locator,
            link_mode=OrganizeMode.COPY,
        )


def test_direct_scrape_normalizes_legacy_p115_local_move_to_copy() -> None:
    file_locator = _p115_locator("/115网盘/待整理/S01E01.mkv", is_dir=False)
    request = ScrapeByIdRequest(
        file_path=file_locator.path,
        tmdb_id=1,
        season=1,
        episode=1,
        output_dir="/library",
        file_locator=file_locator,
        allow_local_output=True,
        link_mode=OrganizeMode.MOVE,
    )

    assert request.link_mode == OrganizeMode.COPY


def test_p115_source_requires_an_output_directory() -> None:
    file_locator = _p115_locator("/115网盘/待整理/S01E01.mkv", is_dir=False)
    with pytest.raises(ValueError, match="必须指定输出目录"):
        ScrapeByIdRequest(
            file_path=file_locator.path,
            tmdb_id=1,
            season=1,
            episode=1,
            file_locator=file_locator,
            allow_local_output=True,
            link_mode=OrganizeMode.COPY,
        )


@pytest.mark.parametrize(
    "locator",
    [
        StorageLocator(
            provider=StorageProvider.P115,
            path="/115网盘/待整理/S01E01.mkv",
            file_id="folder-1",
            is_dir=True,
        ),
        StorageLocator(
            provider=StorageProvider.P115,
            path="/115网盘/待整理/S01E01.mkv",
            file_id=None,
            is_dir=False,
        ),
    ],
)
def test_direct_scrape_rejects_invalid_p115_file_locator(
    locator: StorageLocator,
) -> None:
    with pytest.raises(ValueError, match="115 源"):
        ScrapeByIdRequest(
            file_path=locator.path,
            tmdb_id=1,
            season=1,
            episode=1,
            output_dir="/115网盘/媒体库",
            file_locator=locator,
            output_locator=_p115_locator("/115网盘/媒体库"),
            link_mode=OrganizeMode.COPY,
        )


def test_direct_scrape_rejects_file_locator_as_output_directory() -> None:
    file_locator = _p115_locator("/115网盘/待整理/S01E01.mkv", is_dir=False)
    with pytest.raises(ValueError, match="必须是目录"):
        ScrapeByIdRequest(
            file_path=file_locator.path,
            tmdb_id=1,
            season=1,
            episode=1,
            output_dir="/115网盘/媒体库",
            file_locator=file_locator,
            output_locator=_p115_locator("/115网盘/媒体库", is_dir=False),
            link_mode=OrganizeMode.COPY,
        )


def test_direct_scrape_drops_redundant_local_file_locator() -> None:
    request = ScrapeByIdRequest(
        file_path="/incoming/S01E01.mkv",
        tmdb_id=1,
        season=1,
        episode=1,
        output_dir="/library",
        file_locator=StorageLocator(
            provider=StorageProvider.LOCAL,
            path="/incoming/S01E01.mkv",
            is_dir=False,
        ),
        link_mode=OrganizeMode.MOVE,
    )

    assert request.file_locator is None


def test_direct_scrape_rejects_mismatched_file_locator_path() -> None:
    with pytest.raises(ValueError, match="源文件路径不一致"):
        ScrapeByIdRequest(
            file_path="/incoming/S01E01.mkv",
            tmdb_id=1,
            season=1,
            episode=1,
            output_dir="/library",
            file_locator=StorageLocator(
                provider=StorageProvider.LOCAL,
                path="/incoming/other.mkv",
                is_dir=False,
            ),
            link_mode=OrganizeMode.MOVE,
        )


def test_organize_config_rejects_cloud_metadata_directory() -> None:
    with pytest.raises(ValueError, match="元数据目录仅支持本地"):
        OrganizeConfig(metadata_dir="/115网盘/元数据")

    config = OrganizeConfig(metadata_dir="/115网盘备份")
    assert config.metadata_dir == "/115网盘备份"
