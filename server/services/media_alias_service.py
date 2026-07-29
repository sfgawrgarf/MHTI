"""Persistent, confirmed aliases learned from successful media matches."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

import aiosqlite

from server.core.db.connection import DATABASE_PATH
from server.services.recognition_service import normalize_search_text

logger = logging.getLogger(__name__)

_MANUAL_ID_PATTERN = re.compile(
    r"用户手动输入 TMDB ID:\s*(\d+),\s*S(\d+)E(\d+)",
    re.IGNORECASE,
)
_FILE_PATH_PREFIX = "视频文件路径:"
_PARSED_PREFIX = "解析结果:"


def normalize_alias(value: str) -> str:
    """Normalize an alias for exact, Unicode-safe lookup."""
    normalized = normalize_search_text(value).casefold()
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip()


def release_alias_from_path(file_path: str) -> str:
    """Return the full source basename without its media extension."""
    return normalize_search_text(Path(file_path).stem)


@dataclass(frozen=True)
class MediaAliasMatch:
    alias_type: str
    alias: str
    tmdb_id: int
    season: int | None
    episode: int | None
    source: str
    confirmed: bool


class MediaAliasService:
    """Store and resolve aliases without silently replacing conflicts."""

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or DATABASE_PATH

    async def _connect(self) -> aiosqlite.Connection:
        db = await aiosqlite.connect(self.db_path)
        try:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA busy_timeout=30000")
        except Exception:
            await db.close()
            raise
        return db

    async def lookup(
        self,
        *,
        file_path: str,
        parsed_title: str | None,
    ) -> MediaAliasMatch | None:
        """Prefer an exact release alias, then a confirmed series alias."""
        lookups = [
            ("release", normalize_alias(release_alias_from_path(file_path))),
        ]
        if parsed_title:
            lookups.append(("series", normalize_alias(parsed_title)))

        db = await self._connect()
        try:
            for alias_type, normalized in lookups:
                if not normalized:
                    continue
                cursor = await db.execute(
                    """
                    SELECT alias_type, display_alias, tmdb_id, season, episode,
                           source, confirmed
                    FROM media_aliases
                    WHERE alias_type = ? AND normalized_alias = ? AND confirmed = 1
                    """,
                    (alias_type, normalized),
                )
                row = await cursor.fetchone()
                if row is None:
                    continue
                await db.execute(
                    """
                    UPDATE media_aliases
                    SET use_count = use_count + 1, updated_at = CURRENT_TIMESTAMP
                    WHERE alias_type = ? AND normalized_alias = ?
                    """,
                    (alias_type, normalized),
                )
                await db.commit()
                return MediaAliasMatch(
                    alias_type=row["alias_type"],
                    alias=row["display_alias"],
                    tmdb_id=int(row["tmdb_id"]),
                    season=None if row["season"] is None else int(row["season"]),
                    episode=None if row["episode"] is None else int(row["episode"]),
                    source=row["source"],
                    confirmed=bool(row["confirmed"]),
                )
        finally:
            await db.close()
        return None

    async def remember_confirmed(
        self,
        *,
        file_path: str,
        parsed_title: str | None,
        tmdb_id: int,
        season: int,
        episode: int,
        canonical_titles: list[str | None],
        source: str = "manual",
    ) -> int:
        """Remember release, parsed, and canonical aliases after confirmation."""
        aliases: list[tuple[str, str, int | None, int | None]] = [
            ("release", release_alias_from_path(file_path), season, episode),
        ]
        if parsed_title:
            aliases.append(("series", parsed_title, None, None))
        aliases.extend(
            ("series", title, None, None)
            for title in canonical_titles
            if title
        )

        db = await self._connect()
        inserted = 0
        try:
            for alias_type, display_alias, alias_season, alias_episode in aliases:
                inserted += await self._remember_one(
                    db,
                    alias_type=alias_type,
                    display_alias=display_alias,
                    tmdb_id=tmdb_id,
                    season=alias_season,
                    episode=alias_episode,
                    source=source,
                    confirmed=True,
                )
            await db.commit()
        finally:
            await db.close()
        return inserted

    async def _remember_one(
        self,
        db: aiosqlite.Connection,
        *,
        alias_type: str,
        display_alias: str,
        tmdb_id: int,
        season: int | None,
        episode: int | None,
        source: str,
        confirmed: bool,
    ) -> int:
        normalized = normalize_alias(display_alias)
        if not normalized:
            return 0
        cursor = await db.execute(
            """
            SELECT tmdb_id, season, episode
            FROM media_aliases
            WHERE alias_type = ? AND normalized_alias = ?
            """,
            (alias_type, normalized),
        )
        existing = await cursor.fetchone()
        if existing is not None:
            same_identity = int(existing["tmdb_id"]) == tmdb_id
            if alias_type == "release":
                same_identity = (
                    same_identity
                    and existing["season"] == season
                    and existing["episode"] == episode
                )
            if not same_identity:
                logger.warning(
                    "Disabling conflicting media alias %s:%s "
                    "(existing TMDB %s, new TMDB %s)",
                    alias_type,
                    normalized,
                    existing["tmdb_id"],
                    tmdb_id,
                )
                await db.execute(
                    """
                    UPDATE media_aliases
                    SET source = 'conflict', confirmed = 0,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE alias_type = ? AND normalized_alias = ?
                    """,
                    (alias_type, normalized),
                )
                return 0
            await db.execute(
                """
                UPDATE media_aliases
                SET display_alias = ?, source = ?,
                    confirmed = MAX(confirmed, ?),
                    updated_at = CURRENT_TIMESTAMP
                WHERE alias_type = ? AND normalized_alias = ?
                """,
                (
                    display_alias,
                    source,
                    int(confirmed),
                    alias_type,
                    normalized,
                ),
            )
            return 0

        await db.execute(
            """
            INSERT INTO media_aliases (
                alias_type, normalized_alias, display_alias, tmdb_id,
                season, episode, source, confirmed
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                alias_type,
                normalized,
                display_alias,
                tmdb_id,
                season,
                episode,
                source,
                int(confirmed),
            ),
        )
        return 1

    async def backfill_confirmed_history(self) -> int:
        """Idempotently learn aliases from successful manual history rows."""
        db = await self._connect()
        inserted = 0
        try:
            cursor = await db.execute(
                """
                SELECT folder_path, title, original_title, scrape_logs
                FROM history_records
                WHERE status = 'success'
                  AND scrape_logs LIKE '%用户手动输入 TMDB ID:%'
                """
            )
            rows = await cursor.fetchall()
            for row in rows:
                extracted = self._extract_history_mapping(row["scrape_logs"])
                if extracted is None:
                    continue
                file_path, parsed_title, tmdb_id, season, episode = extracted
                aliases: list[tuple[str, str, int | None, int | None]] = [
                    ("release", release_alias_from_path(file_path), season, episode),
                ]
                if parsed_title:
                    aliases.append(("series", parsed_title, None, None))
                for title in (row["title"], row["original_title"]):
                    if title:
                        aliases.append(("series", title, None, None))
                for alias_type, display_alias, alias_season, alias_episode in aliases:
                    inserted += await self._remember_one(
                        db,
                        alias_type=alias_type,
                        display_alias=display_alias,
                        tmdb_id=tmdb_id,
                        season=alias_season,
                        episode=alias_episode,
                        source="history_backfill",
                        confirmed=True,
                    )
            await db.commit()
        except aiosqlite.OperationalError as exc:
            # Fresh or legacy test databases may not have history yet.
            error = str(exc).lower()
            if "no such table" not in error and "no such column" not in error:
                raise
        finally:
            await db.close()
        return inserted

    @staticmethod
    def _extract_history_mapping(
        raw_logs: str | None,
    ) -> tuple[str, str | None, int, int, int] | None:
        try:
            steps = json.loads(raw_logs or "[]")
        except json.JSONDecodeError:
            return None

        file_path: str | None = None
        parsed_title: str | None = None
        identity: tuple[int, int, int] | None = None
        for step in steps:
            for entry in step.get("logs", []):
                message = str(entry.get("message", ""))
                if message.startswith(_FILE_PATH_PREFIX):
                    file_path = message.removeprefix(_FILE_PATH_PREFIX).strip()
                elif message.startswith(_PARSED_PREFIX):
                    parsed = message.removeprefix(_PARSED_PREFIX).strip()
                    parsed_title = re.sub(r"\s+S(?:\d+|\?)E(?:\d+|\?)$", "", parsed)
                match = _MANUAL_ID_PATTERN.search(message)
                if match:
                    identity = tuple(int(group) for group in match.groups())

        if not file_path or identity is None:
            return None
        tmdb_id, season, episode = identity
        return file_path, parsed_title, tmdb_id, season, episode
