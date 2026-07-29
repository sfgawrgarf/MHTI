from datetime import date

from server.models.tmdb import TMDBEpisode, TMDBSearchResult, TMDBSeason
from server.services.recognition_service import (
    build_search_title_variants,
    match_episode_from_titles,
    select_series_candidate,
)


def test_query_ladder_removes_adult_ova_release_suffixes() -> None:
    assert "HHH トリプルエッチ" in build_search_title_variants(
        "HHH トリプルエッチ 3rd みゆき編"
    )
    assert "花粉少女注意報!" in build_search_title_variants(
        "花粉少女注意報！～ ～ ATTACK NO.3"
    )
    assert "彼女が見舞いに来ない理由(わけ)" in build_search_title_variants(
        "彼女が見舞いに来ない理由（わけ） 理由3"
    )


def test_query_ladder_removes_fullwidth_quality_tag() -> None:
    variants = build_search_title_variants(
        "町ぐるみの罠～白濁にまみれた肢体～ 【720P】"
    )
    assert all("720P" not in item for item in variants[1:])


def test_series_candidate_requires_a_clear_title_match() -> None:
    correct = TMDBSearchResult(
        id=97376,
        name="她不来探望的原因",
        original_name="彼女が見舞いに来ない理由",
        adult=True,
    )
    unrelated = TMDBSearchResult(
        id=1,
        name="无关作品",
        original_name="別の作品",
        adult=True,
    )

    match = select_series_candidate(
        [unrelated, correct],
        ["彼女が見舞いに来ない理由"],
    )

    assert match is not None
    assert match.candidate.id == 97376
    assert match.score > 0.9


def test_episode_match_uses_japanese_title_and_release_month() -> None:
    localized = TMDBSeason(
        season_number=1,
        name="第一季",
        episodes=[
            TMDBEpisode(
                episode_number=1,
                name="奥尔加与克洛伊",
                air_date=date(2012, 1, 27),
            ),
            TMDBEpisode(
                episode_number=2,
                name="艾丽西亚与普里姆",
                air_date=date(2012, 6, 29),
            ),
        ],
    )
    japanese = TMDBSeason(
        season_number=1,
        name="シーズン1",
        episodes=[
            TMDBEpisode(
                episode_number=1,
                name="オルガ×クロエ 黒の城、崩落編",
                air_date=date(2012, 1, 27),
            ),
            TMDBEpisode(
                episode_number=2,
                name="アリシア×プリム 奉仕国家抗い編",
                air_date=date(2012, 6, 29),
            ),
        ],
    )

    match = match_episode_from_titles(
        file_path=(
            "/incoming/里番/2012年1月作品合集/"
            "黒獣～オルガ×クロエ 黒の城、崩落編～.strm"
        ),
        seasons_by_language=[[localized], [japanese]],
    )

    assert match is not None
    assert (match.season, match.episode) == (1, 1)
    assert match.score > 0.9


def test_episode_match_rejects_generic_or_ambiguous_titles() -> None:
    season = TMDBSeason(
        season_number=1,
        name="第 1 季",
        episodes=[
            TMDBEpisode(episode_number=1, name="第 1 集"),
            TMDBEpisode(episode_number=2, name="第 2 集"),
        ],
    )

    assert match_episode_from_titles(
        file_path="/incoming/作品 未知篇名.strm",
        seasons_by_language=[[season]],
    ) is None
