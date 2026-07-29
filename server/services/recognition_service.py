"""Title expansion and episode matching for Japanese adult animation."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path

from server.models.tmdb import TMDBSearchResult, TMDBSeason

_TECHNICAL_BRACKET = re.compile(
    r"[\[【](?:"
    r"\d{3,4}p|4k|uhd|fhd|hd|10bit|8bit|x26[45]|h\.?26[45]|hevc|"
    r"aac|flac|mkv|mp4|bdrip|bluray|web[ .-]?dl"
    r")(?:[^\]】]*)[\]】]",
    re.IGNORECASE,
)
_QUOTED_SUBTITLE = re.compile(r"\s*[「『].*?[」』]\s*")
_ANIMATION_MARKER = re.compile(r"\b(?:THE\s+ANIMATION|ANIMATION|OVA|OAD|ONA)\b", re.IGNORECASE)
_RELEASE_SUFFIXES = [
    re.compile(
        r"\s+(?:ATTACK\s*NO|INSERT|DESIRE|MEMORIAL|REASON|ANIME)"
        r"\s*[.:：．#＃]?\s*\d+\b.*$",
        re.IGNORECASE,
    ),
    re.compile(r"\s+理由\s*\d+\b.*$", re.IGNORECASE),
    re.compile(r"\s+\d+(?:ST|ND|RD|TH)\b.*$", re.IGNORECASE),
    re.compile(
        r"\s+(?:第\s*)?[\d一二三四五六七八九十]+"
        r"(?:話|话|集|回|章|巻|卷|夜|幕|枚目)\b.*$",
        re.IGNORECASE,
    ),
    re.compile(r"\s+(?:前編|後編|前篇|後篇|上巻|下巻|中編|中篇)\b.*$"),
]
_YEAR_MONTH_PATTERNS = [
    re.compile(r"((?:19|20)\d{2})年\s*(\d{1,2})月"),
    re.compile(r"((?:19|20)\d{2})[-_.](\d{1,2})(?:[-_.]\d{1,2})?"),
]
_GENERIC_EPISODE_TITLE = re.compile(
    r"^(?:第)?\d+(?:話|话|集|回|章)?$|^(?:episode|ep)\d+$",
    re.IGNORECASE,
)


def normalize_search_text(value: str) -> str:
    """Normalize a title for searching without changing the on-disk name."""
    text = unicodedata.normalize("NFKC", value or "")
    text = text.translate(
        str.maketrans(
            {
                "〜": "~",
                "～": "~",
                "﹏": "~",
                "・": " ",
                "／": "/",
                "：": ":",
                "！": "!",
                "？": "?",
            }
        )
    )
    text = re.sub(r"\s+", " ", text)
    return text.strip(" \t\r\n-_.")


def compact_title(value: str) -> str:
    """Return a punctuation-insensitive comparison key."""
    normalized = normalize_search_text(value).casefold()
    return re.sub(r"[\W_]+", "", normalized, flags=re.UNICODE)


def _append_unique(values: list[str], value: str | None) -> None:
    if not value:
        return
    normalized = normalize_search_text(value)
    if len(compact_title(normalized)) < 2:
        return
    if normalized not in values:
        values.append(normalized)


def build_search_title_variants(title: str, *, limit: int = 6) -> list[str]:
    """Build a bounded, deterministic query ladder for adult OVA filenames."""
    variants: list[str] = []
    base = normalize_search_text(title)
    _append_unique(variants, base)

    cleaned = _TECHNICAL_BRACKET.sub(" ", base)
    cleaned = _QUOTED_SUBTITLE.sub(" ", cleaned)
    cleaned = _ANIMATION_MARKER.sub(" ", cleaned)
    cleaned = re.sub(r"~\s*~", "~", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ~-_.")
    _append_unique(variants, cleaned)

    release_core = cleaned
    for pattern in _RELEASE_SUFFIXES:
        release_core = pattern.sub("", release_core).strip(" ~-_.")
    _append_unique(variants, release_core)

    # Japanese releases often put the episode subtitle after a quoted section
    # or a second wave-dash block. Keep this as a later, less specific fallback.
    if "「" in base or "『" in base:
        _append_unique(variants, re.split(r"[「『]", base, maxsplit=1)[0])

    wave_parts = [part.strip() for part in re.split(r"~+", release_core) if part.strip()]
    if len(wave_parts) > 1:
        _append_unique(variants, wave_parts[0])

    return variants[:limit]


def merge_search_results(
    current: list[TMDBSearchResult],
    incoming: list[TMDBSearchResult],
) -> list[TMDBSearchResult]:
    """Merge TMDB candidates by ID while preserving discovery order."""
    merged = list(current)
    seen = {item.id for item in merged}
    for item in incoming:
        if item.id not in seen:
            merged.append(item)
            seen.add(item.id)
    return merged


def candidate_title_score(
    candidate: TMDBSearchResult,
    query_titles: list[str],
) -> float:
    """Score a TMDB candidate against all attempted titles."""
    candidate_titles = [candidate.name, candidate.original_name or ""]
    best = 0.0
    for query in query_titles:
        query_key = compact_title(query)
        if len(query_key) < 2:
            continue
        for candidate_title in candidate_titles:
            candidate_key = compact_title(candidate_title)
            if len(candidate_key) < 2:
                continue
            if query_key == candidate_key:
                score = 1.0
            elif min(len(query_key), len(candidate_key)) >= 4 and (
                query_key in candidate_key or candidate_key in query_key
            ):
                length_ratio = min(len(query_key), len(candidate_key)) / max(
                    len(query_key), len(candidate_key)
                )
                score = 0.88 + 0.1 * length_ratio
            else:
                score = SequenceMatcher(None, query_key, candidate_key).ratio()
            best = max(best, score)
    return min(best, 1.0)


@dataclass(frozen=True)
class SeriesCandidateMatch:
    candidate: TMDBSearchResult
    score: float
    margin: float


def select_series_candidate(
    candidates: list[TMDBSearchResult],
    query_titles: list[str],
    *,
    minimum_score: float = 0.86,
    minimum_margin: float = 0.10,
) -> SeriesCandidateMatch | None:
    """Select only a clearly leading title match."""
    if not candidates:
        return None
    ranked = sorted(
        ((candidate_title_score(item, query_titles), item) for item in candidates),
        key=lambda pair: pair[0],
        reverse=True,
    )
    best_score, best = ranked[0]
    second_score = ranked[1][0] if len(ranked) > 1 else 0.0
    margin = best_score - second_score
    if best_score < minimum_score:
        return None
    if len(ranked) > 1 and margin < minimum_margin:
        return None
    return SeriesCandidateMatch(candidate=best, score=best_score, margin=margin)


def extract_release_year_month(file_path: str) -> tuple[int, int] | None:
    """Extract the most specific release year/month present in the path."""
    matches: list[tuple[int, int]] = []
    for pattern in _YEAR_MONTH_PATTERNS:
        for match in pattern.finditer(file_path):
            year, month = int(match.group(1)), int(match.group(2))
            if 1950 <= year <= 2100 and 1 <= month <= 12:
                matches.append((year, month))
    return matches[-1] if matches else None


@dataclass(frozen=True)
class EpisodeMatch:
    season: int
    episode: int
    score: float
    margin: float
    matched_title: str


def _episode_title_score(filename_key: str, episode_title: str) -> float:
    title_key = compact_title(episode_title)
    if len(title_key) < 4 or _GENERIC_EPISODE_TITLE.fullmatch(title_key):
        return 0.0
    if title_key in filename_key:
        return 1.0
    longest = SequenceMatcher(None, title_key, filename_key).find_longest_match()
    coverage = longest.size / len(title_key)
    ratio = SequenceMatcher(None, title_key, filename_key).ratio()
    return max(coverage, ratio)


def match_episode_from_titles(
    *,
    file_path: str,
    seasons_by_language: list[list[TMDBSeason]],
    season_hint: int | None = None,
    minimum_score: float = 0.82,
    minimum_margin: float = 0.08,
) -> EpisodeMatch | None:
    """Match an OVA episode by multilingual episode title and release month."""
    filename_key = compact_title(Path(file_path).stem)
    release_month = extract_release_year_month(file_path)
    candidates: dict[tuple[int, int], dict[str, object]] = {}

    for seasons in seasons_by_language:
        for season in seasons:
            if season_hint is not None and season.season_number != season_hint:
                continue
            for episode in season.episodes or []:
                key = (season.season_number, episode.episode_number)
                entry = candidates.setdefault(
                    key,
                    {"titles": [], "dates": []},
                )
                if episode.name:
                    titles = entry["titles"]
                    assert isinstance(titles, list)
                    if episode.name not in titles:
                        titles.append(episode.name)
                if episode.air_date:
                    dates = entry["dates"]
                    assert isinstance(dates, list)
                    dates.append(episode.air_date)

    ranked: list[tuple[float, tuple[int, int], str]] = []
    for key, entry in candidates.items():
        titles = entry["titles"]
        dates = entry["dates"]
        assert isinstance(titles, list)
        assert isinstance(dates, list)
        title_scores = [(_episode_title_score(filename_key, title), title) for title in titles]
        title_score, matched_title = max(title_scores, default=(0.0, ""))
        date_bonus = 0.0
        if release_month and any(
            isinstance(item, date)
            and (item.year, item.month) == release_month
            for item in dates
        ):
            date_bonus = 0.06
        ranked.append((min(title_score + date_bonus, 1.0), key, matched_title))

    if not ranked:
        return None
    ranked.sort(key=lambda item: item[0], reverse=True)
    best_score, (season, episode), matched_title = ranked[0]
    second_score = ranked[1][0] if len(ranked) > 1 else 0.0
    margin = best_score - second_score
    if best_score < minimum_score:
        return None
    if len(ranked) > 1 and margin < minimum_margin:
        return None
    return EpisodeMatch(
        season=season,
        episode=episode,
        score=best_score,
        margin=margin,
        matched_title=matched_title,
    )
