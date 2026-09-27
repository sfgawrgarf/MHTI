"""Tests for deterministic title normalization and release aliases."""

from server.application.recognition import (
    build_search_title_variants,
    compact_title,
    normalize_search_text,
    release_alias_from_path,
)


def test_title_normalization_and_compaction_are_deterministic() -> None:
    assert normalize_search_text("  作品～名・特别篇  ") == "作品~名 特别篇"
    assert compact_title("作品~名") == compact_title("作品 名")
    assert normalize_search_text("---") == ""


def test_query_ladder_removes_adult_ova_release_suffixes() -> None:
    variants = build_search_title_variants("HHH トリプルエッチ 3rd みゆき編")
    assert "HHH トリプルエッチ" in variants
    assert "花粉少女注意報!" in build_search_title_variants(
        "花粉少女注意報！～ ～ ATTACK NO.3"
    )
    assert "彼女が見舞いに来ない理由(わけ)" in build_search_title_variants(
        "彼女が見舞いに来ない理由（わけ） 理由3"
    )


def test_query_ladder_removes_technical_tags_and_quoted_suffixes() -> None:
    variants = build_search_title_variants(
        "町ぐるみの罠～白濁にまみれた肢体～ 【720P】"
    )
    assert all("720P" not in item for item in variants[1:])

    quoted = build_search_title_variants("作品名「第1話」～副題～")
    assert "作品名" in quoted
    assert build_search_title_variants("AB", limit=1) == ["AB"]


def test_release_alias_uses_normalized_file_stem() -> None:
    assert release_alias_from_path("/incoming/作品～名・01.strm") == "作品~名 01"
