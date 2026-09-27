"""Additive database compatibility for installations from the forked line.

The upstream refactor creates the new schema, but existing MHTI databases may
contain later fork fields and media identity tables. This module only adds
known columns, indexes, triggers, and tables; it never renames or drops user
data. The caller owns the transaction.
"""

from __future__ import annotations

import aiosqlite


_MISSING_COLUMNS: dict[str, tuple[tuple[str, str], ...]] = {
    "manual_jobs": (
        ("metadata_dir", "TEXT DEFAULT ''"),
        ("source", "TEXT DEFAULT 'manual'"),
        ("advanced_settings", "TEXT"),
        ("scan_locator", "TEXT"),
        ("target_locator", "TEXT"),
        ("metadata_locator", "TEXT"),
        ("allow_local_output", "INTEGER DEFAULT 0"),
    ),
    "scheduled_tasks": (
        ("last_attempt", "TEXT"),
        ("last_status", "TEXT"),
        ("last_error", "TEXT"),
        ("retry_count", "INTEGER DEFAULT 0"),
    ),
    "history_records": (
        ("display_id", "INTEGER"),
        ("manual_job_id", "INTEGER"),
        ("title", "TEXT"),
        ("original_title", "TEXT"),
        ("plot", "TEXT"),
        ("tags", "TEXT"),
        ("cover_url", "TEXT"),
        ("poster_url", "TEXT"),
        ("thumb_url", "TEXT"),
        ("release_date", "TEXT"),
        ("rating", "REAL"),
        ("votes", "INTEGER"),
        ("translator", "TEXT"),
        ("scrape_logs", "TEXT"),
        ("conflict_type", "TEXT"),
        ("conflict_data", "TEXT"),
        ("season_number", "INTEGER"),
        ("episode_number", "INTEGER"),
        ("episode_title", "TEXT"),
        ("episode_overview", "TEXT"),
        ("episode_still_url", "TEXT"),
        ("episode_air_date", "TEXT"),
        ("source", "TEXT DEFAULT 'manual'"),
        ("scrape_job_id", "TEXT"),
        ("file_fingerprint", "TEXT"),
        ("tmdb_id", "INTEGER"),
        ("started_at", "TEXT"),
        ("timeout_step", "TEXT"),
        ("timeout_seconds", "INTEGER"),
    ),
    "scrape_jobs": (
        ("link_mode", "TEXT"),
        ("advanced_settings", "TEXT"),
        ("file_locator", "TEXT"),
        ("output_locator", "TEXT"),
        ("metadata_locator", "TEXT"),
        ("allow_local_output", "INTEGER DEFAULT 0"),
        ("replaces_job_id", "TEXT"),
        ("replaced_by_job_id", "TEXT"),
        ("correction_history_id", "TEXT"),
        ("correction_tmdb_id", "INTEGER"),
        ("correction_season", "INTEGER"),
        ("correction_episode", "INTEGER"),
        ("continuation_history_id", "TEXT"),
        ("file_action", "TEXT"),
        ("selection_log", "TEXT"),
        ("skip_emby_check", "INTEGER DEFAULT 0"),
    ),
    "watched_folders": (
        ("mode", "TEXT DEFAULT 'realtime'"),
        ("output_dir", "TEXT"),
        ("provider", "TEXT DEFAULT 'local'"),
        ("file_id", "TEXT"),
    ),
}


async def _add_missing_columns(
    db: aiosqlite.Connection,
    table: str,
    expected: tuple[tuple[str, str], ...],
) -> None:
    """Add known columns without using broad exceptions as control flow."""
    async with db.execute(f"PRAGMA table_info({table})") as cursor:
        columns = {row[1] for row in await cursor.fetchall()}
    if not columns:
        raise aiosqlite.OperationalError(f"Missing {table} table")
    for name, column_type in expected:
        if name not in columns:
            await db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {column_type}")


async def _create_media_identity_tables(db: aiosqlite.Connection) -> None:
    """Create the additive AI recognition and media version tables."""
    await db.execute("""
        CREATE TABLE IF NOT EXISTS media_identities (
            identity_key TEXT PRIMARY KEY,
            tmdb_id INTEGER NOT NULL,
            season INTEGER NOT NULL,
            episode INTEGER NOT NULL,
            title TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS media_versions (
            source_fingerprint TEXT PRIMARY KEY,
            identity_key TEXT NOT NULL,
            source_path TEXT NOT NULL,
            target_path TEXT,
            quality_score INTEGER NOT NULL DEFAULT 0,
            quality_labels TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(identity_key) REFERENCES media_identities(identity_key)
        )
    """)
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_media_versions_identity "
        "ON media_versions(identity_key)"
    )
    await db.execute("""
        CREATE TABLE IF NOT EXISTS media_aliases (
            alias_type TEXT NOT NULL,
            normalized_alias TEXT NOT NULL,
            display_alias TEXT NOT NULL,
            tmdb_id INTEGER NOT NULL,
            season INTEGER,
            episode INTEGER,
            source TEXT NOT NULL DEFAULT 'manual',
            confirmed INTEGER NOT NULL DEFAULT 0,
            use_count INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(alias_type, normalized_alias)
        )
    """)
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_media_aliases_tmdb "
        "ON media_aliases(tmdb_id)"
    )


async def ensure_compatibility_schema(db: aiosqlite.Connection) -> None:
    """Ensure fork-era schema objects exist using additive, idempotent changes."""
    for table, columns in _MISSING_COLUMNS.items():
        await _add_missing_columns(db, table, columns)

    await db.execute("""
        CREATE TRIGGER IF NOT EXISTS admin_singleton_insert
        BEFORE INSERT ON admin
        WHEN EXISTS (SELECT 1 FROM admin)
        BEGIN
            SELECT RAISE(ABORT, 'only one administrator is allowed');
        END
    """)

    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_manual_jobs_status_created "
        "ON manual_jobs(status, created_at)"
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_history_executed_at "
        "ON history_records(executed_at DESC)"
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_history_status_executed "
        "ON history_records(status, executed_at DESC)"
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_history_manual_executed "
        "ON history_records(manual_job_id, executed_at DESC)"
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_history_scrape_job "
        "ON history_records(scrape_job_id)"
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_history_fingerprint_status "
        "ON history_records(file_fingerprint, status)"
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_history_folder_status "
        "ON history_records(folder_path, status)"
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_scrape_jobs_status_created "
        "ON scrape_jobs(status, created_at)"
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_scrape_jobs_source_status "
        "ON scrape_jobs(source, source_id, status)"
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_scrape_jobs_history_record "
        "ON scrape_jobs(history_record_id)"
    )

    await _create_media_identity_tables(db)
