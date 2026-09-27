"""Deterministic title normalization used by AI and alias matching."""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path


def normalize_search_text(value: str) -> str:
    """Normalize a title for search without changing the stored filename."""
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
    """Return a punctuation-insensitive title key."""
    return re.sub(
        r"[\W_]+",
        "",
        normalize_search_text(value).casefold(),
        flags=re.UNICODE,
    )


_TECHNICAL_BRACKET = re.compile(
    r"[\[【](?:\d{3,4}p|4k|uhd|fhd|hd|10bit|8bit|x26[45]|h\.?26[45]|"
    r"hevc|aac|flac|mkv|mp4|bdrip|bluray|web[ .-]?dl)(?:[^\]】]*)[\]】]",
    re.IGNORECASE,
)
_QUOTED_SUBTITLE = re.compile(r"\s*[「『].*?[」』]\s*")
_ANIMATION_MARKER = re.compile(
    r"\b(?:THE\s+ANIMATION|ANIMATION|OVA|OAD|ONA)\b",
    re.IGNORECASE,
)
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


def build_search_title_variants(title: str, *, limit: int = 6) -> list[str]:
    """Build a bounded, deterministic query ladder for release filenames."""
    variants: list[str] = []

    def append(value: str | None) -> None:
        normalized = normalize_search_text(value or "")
        if len(compact_title(normalized)) >= 2 and normalized not in variants:
            variants.append(normalized)

    base = normalize_search_text(title)
    append(base)
    cleaned = _TECHNICAL_BRACKET.sub(" ", base)
    cleaned = _QUOTED_SUBTITLE.sub(" ", cleaned)
    cleaned = _ANIMATION_MARKER.sub(" ", cleaned)
    cleaned = re.sub(r"~\s*~", "~", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ~-_.")
    append(cleaned)

    release_core = cleaned
    for pattern in _RELEASE_SUFFIXES:
        release_core = pattern.sub("", release_core).strip(" ~-_.")
    append(release_core)

    if "「" in base or "『" in base:
        append(re.split(r"[「『]", base, maxsplit=1)[0])
    wave_parts = [part.strip() for part in re.split(r"~+", release_core) if part.strip()]
    if len(wave_parts) > 1:
        append(wave_parts[0])
    return variants[:limit]


def release_alias_from_path(file_path: str) -> str:
    """Return the source basename without its media extension."""
    return normalize_search_text(Path(file_path).stem)
