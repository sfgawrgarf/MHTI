"""Logical media identity, fingerprinting, and non-destructive version decisions."""

import hashlib
import re
from pathlib import Path

from server.core.db.connection import db_context
from server.models.ai import VersionPolicy, VersionPreview


class MediaIdentityService:
    @staticmethod
    def identity_key(tmdb_id: int, season: int, episode: int) -> str:
        return f"tmdb:{tmdb_id}:s{season:02d}:e{episode:02d}"

    @staticmethod
    def fingerprint(path: str) -> str:
        source = Path(path)
        digest = hashlib.blake2s()
        digest.update(source.name.encode("utf-8", errors="ignore"))
        if source.exists() and source.is_file():
            with source.open("rb") as handle:
                digest.update(handle.read(1024 * 1024))
            digest.update(str(source.stat().st_size).encode())
        else:
            digest.update(path.encode("utf-8", errors="ignore"))
        return digest.hexdigest()

    @staticmethod
    def score_quality(path: str) -> tuple[int, list[str]]:
        name = Path(path).name.lower()
        score = 0
        labels: list[str] = []
        for pattern, points, label in [
            (r"(2160p|4k|uhd)", 100, "2160p"),
            (r"1080p|fhd", 75, "1080p"),
            (r"720p|hd", 50, "720p"),
            (r"480p|dvd", 25, "480p/DVD"),
        ]:
            if re.search(pattern, name):
                score = max(score, points)
                labels.append(label)
                break
        for pattern, points, label in [
            (r"dv|dolby[ .-]?vision", 12, "Dolby Vision"),
            (r"hdr10[+]|hdr", 8, "HDR"),
            (r"remux", 10, "Remux"),
            (r"bluray|bdrip", 6, "BluRay"),
            (r"web[ .-]?dl", 4, "WEB-DL"),
            (r"x265|hevc|h[.]265", 3, "HEVC"),
        ]:
            if re.search(pattern, name):
                score += points
                labels.append(label)
        return score, labels

    async def preview(
        self,
        *,
        file_path: str,
        tmdb_id: int,
        season: int,
        episode: int,
        policy: VersionPolicy,
    ) -> VersionPreview:
        key = self.identity_key(tmdb_id, season, episode)
        fingerprint = self.fingerprint(file_path)
        score, labels = self.score_quality(file_path)
        async with db_context() as db:
            cursor = await db.execute(
                "SELECT source_path, target_path, quality_score, quality_labels FROM media_versions "
                "WHERE identity_key = ? ORDER BY quality_score DESC",
                (key,),
            )
            rows = await cursor.fetchall()
        existing = [
            {
                "source_path": row["source_path"],
                "target_path": row["target_path"],
                "quality_score": row["quality_score"],
                "quality_labels": row["quality_labels"].split(",") if row["quality_labels"] else [],
            }
            for row in rows
        ]
        if any(row["source_path"] == file_path for row in existing):
            action, reason = "skip", "该源文件已经记录，不会重复刮削"
        elif not existing:
            action, reason = "add", "未找到同一逻辑媒体的已记录版本"
        elif policy == VersionPolicy.COEXIST:
            action, reason = "coexist", "策略为多版本共存"
        elif policy == VersionPolicy.SKIP:
            action, reason = "skip", "策略为已有版本即跳过"
        elif policy == VersionPolicy.ARCHIVE:
            action, reason = "archive", "策略为保留现有媒体并归档新版本"
        elif score > max(int(row["quality_score"]) for row in existing):
            action, reason = "replace_candidate", "新版本质量评分更高；执行前仍需确认"
        else:
            action, reason = "archive", "现有版本质量评分不低于新版本"
        return VersionPreview(
            identity_key=key,
            source_fingerprint=fingerprint,
            quality_score=score,
            quality_labels=labels,
            action=action,
            reason=reason,
            existing_versions=existing,
        )

    async def record(
        self,
        *,
        file_path: str,
        target_path: str | None,
        tmdb_id: int,
        season: int,
        episode: int,
        title: str | None,
    ) -> VersionPreview:
        preview = await self.preview(
            file_path=file_path,
            tmdb_id=tmdb_id,
            season=season,
            episode=episode,
            policy=VersionPolicy.COEXIST,
        )
        async with db_context() as db:
            await db.execute(
                "INSERT INTO media_identities (identity_key, tmdb_id, season, episode, title) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT(identity_key) DO UPDATE SET title=excluded.title",
                (preview.identity_key, tmdb_id, season, episode, title),
            )
            await db.execute(
                "INSERT INTO media_versions (source_fingerprint, identity_key, source_path, target_path, "
                "quality_score, quality_labels) VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(source_fingerprint) DO UPDATE SET target_path=excluded.target_path, "
                "quality_score=excluded.quality_score, quality_labels=excluded.quality_labels",
                (
                    preview.source_fingerprint,
                    preview.identity_key,
                    file_path,
                    target_path,
                    preview.quality_score,
                    ",".join(preview.quality_labels),
                ),
            )
            await db.commit()
        return preview
