from datetime import datetime, timedelta

from server.services.correction_backup_service import CorrectionBackupService


def test_backup_copies_episode_sidecars_and_shared_metadata(tmp_path):
    show_dir = tmp_path / "Show"
    season_dir = show_dir / "Season 01"
    season_dir.mkdir(parents=True)
    media = season_dir / "S01E01.strm"
    for path in (
        media,
        season_dir / "S01E01.nfo",
        season_dir / "season.nfo",
        season_dir / "poster.jpg",
        show_dir / "tvshow.nfo",
        show_dir / "fanart.jpg",
    ):
        path.write_text(path.name, encoding="utf-8")

    service = CorrectionBackupService(backup_root=tmp_path / "backups")
    backup = service.backup_for_correction(media, "history-old")

    assert (backup / "season-S01E01.strm").is_file()
    assert (backup / "season-S01E01.nfo").is_file()
    assert (backup / "season-season.nfo").is_file()
    assert (backup / "show-tvshow.nfo").is_file()
    assert (backup / "show-fanart.jpg").is_file()


def test_backup_cleanup_removes_only_expired_backup_directories(tmp_path):
    root = tmp_path / "backups"
    expired = root / "expired"
    retained = root / "retained"
    expired.mkdir(parents=True)
    retained.mkdir()
    old_time = (datetime.now() - timedelta(days=8)).timestamp()
    expired.touch()
    import os
    os.utime(expired, (old_time, old_time))

    service = CorrectionBackupService(backup_root=root)
    service.cleanup_expired()

    assert not expired.exists()
    assert retained.exists()
