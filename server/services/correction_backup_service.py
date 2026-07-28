"""Bounded, recoverable backups for successful-history corrections."""

import shutil
from datetime import datetime, timedelta
from pathlib import Path

from server.core.database import DATABASE_PATH


class CorrectionBackupService:
    """Copy only the media files that a correction can overwrite or relabel."""

    retention_days = 7

    def __init__(self, backup_root: Path | None = None):
        self.backup_root = backup_root or DATABASE_PATH.parent / "correction-backups"

    def backup_for_correction(self, media_path: Path, history_id: str) -> Path:
        if not media_path.is_file():
            raise FileNotFoundError(f"当前已整理文件不存在: {media_path}")

        self.cleanup_expired()
        backup_dir = self.backup_root / f"{datetime.now():%Y%m%d-%H%M%S}-{history_id}"
        backup_dir.mkdir(parents=True, exist_ok=False)

        candidates = {media_path}
        # 同名 sidecar（NFO 和常见图片）；不使用 glob，避免备份到无关文件。
        for suffix in (".nfo", ".jpg", ".jpeg", ".png", ".webp"):
            candidates.add(media_path.with_suffix(suffix))

        season_dir = media_path.parent
        show_dir = season_dir.parent
        for directory in (season_dir, show_dir):
            for name in ("season.nfo", "tvshow.nfo", "poster.jpg", "fanart.jpg"):
                candidates.add(directory / name)

        copied = 0
        for candidate in sorted(candidates):
            if not candidate.is_file():
                continue
            destination = backup_dir / self._backup_name(media_path, candidate)
            shutil.copy2(candidate, destination)
            copied += 1
        if copied == 0:
            raise RuntimeError("未找到可备份的当前媒体文件")
        return backup_dir

    def cleanup_expired(self) -> None:
        if not self.backup_root.exists():
            return
        cutoff = datetime.now() - timedelta(days=self.retention_days)
        for entry in self.backup_root.iterdir():
            if entry.is_dir() and datetime.fromtimestamp(entry.stat().st_mtime) < cutoff:
                shutil.rmtree(entry)

    @staticmethod
    def _backup_name(media_path: Path, candidate: Path) -> str:
        if candidate.parent == media_path.parent:
            return f"season-{candidate.name}"
        if candidate.parent == media_path.parent.parent:
            return f"show-{candidate.name}"
        return f"episode-{candidate.name}"
