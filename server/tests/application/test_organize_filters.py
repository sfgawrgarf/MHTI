"""Regression tests for shared organize filters and source cleanup."""

from server.application.organize_filters import OrganizeFilter
from server.application.source_cleanup import remove_empty_source_parent
from server.models.organize import OrganizeConfig


def test_global_filter_applies_size_extension_and_filename_rules() -> None:
    organize_filter = OrganizeFilter.from_global_config(
        OrganizeConfig(
            min_file_size_mb=1,
            file_type_whitelist=[".mkv"],
            filename_blacklist=["sample"],
        )
    )

    assert organize_filter.allows("Show.mkv", 1024 * 1024)
    assert not organize_filter.allows("Show.mp4", 1024 * 1024)
    assert not organize_filter.allows("Show-sample.mkv", 2 * 1024 * 1024)
    assert not organize_filter.allows("Show.mkv", 1024)


def test_invalid_junk_pattern_does_not_break_filename_cleanup() -> None:
    organize_filter = OrganizeFilter(
        junk_patterns=(r"\[广告\]", "[invalid"),
    )

    assert organize_filter.sanitize_filename("Show[广告].mkv") == "Show.mkv"


def test_source_cleanup_removes_only_an_empty_immediate_parent(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("MHTI_ALLOWED_MEDIA_ROOTS", str(tmp_path))
    source_dir = tmp_path / "incoming"
    source_dir.mkdir()
    source_path = source_dir / "episode.mkv"

    assert remove_empty_source_parent(str(source_path)) is True
    assert not source_dir.exists()
