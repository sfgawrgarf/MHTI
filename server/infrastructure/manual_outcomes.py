"""Preserve batch outcomes when their user-visible history is cleared."""

import aiosqlite

MANUAL_OUTCOMES_DDL = """
    CREATE TABLE IF NOT EXISTS manual_job_outcomes (
        history_record_id TEXT PRIMARY KEY,
        manual_job_id INTEGER NOT NULL,
        scrape_job_id TEXT,
        file_path TEXT NOT NULL,
        status TEXT NOT NULL,
        operation TEXT NOT NULL,
        counted INTEGER NOT NULL
    )
"""

# A restarted ordinary job creates a fresh history attempt with the same job
# ID. Count only its latest attempt, retaining older rows for audit/undo.
# Archived operation metadata keeps failed corrections excluded after undo,
# even though clearing history has removed the original scrape_jobs rows.
CURRENT_HISTORY_PREDICATE = """
    h.total_files > 0 AND h.status != 'replaced'
    AND (COALESCE(j.operation, a.operation, '') != 'rematch' OR h.status = 'success')
    AND (h.scrape_job_id IS NULL OR NOT EXISTS (
        SELECT 1 FROM history_records newer
        WHERE newer.scrape_job_id = h.scrape_job_id
          AND (newer.executed_at > h.executed_at OR
               (newer.executed_at = h.executed_at AND newer.rowid > h.rowid))
    ))
"""


async def ensure_manual_outcomes_schema(db: aiosqlite.Connection) -> None:
    await db.execute(MANUAL_OUTCOMES_DDL)
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_manual_outcomes_parent "
        "ON manual_job_outcomes(manual_job_id)"
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_manual_outcomes_child "
        "ON manual_job_outcomes(scrape_job_id)"
    )


async def archive_manual_outcomes(db: aiosqlite.Connection, cutoff: str | None = None) -> None:
    """Called in the same write transaction as history/child removal."""
    await ensure_manual_outcomes_schema(db)
    await db.execute(
        f"""
        INSERT INTO manual_job_outcomes
            (history_record_id, manual_job_id, scrape_job_id, file_path, status, operation, counted)
        SELECT h.id, h.manual_job_id, h.scrape_job_id, COALESCE(j.file_path, a.file_path, h.folder_path),
               CASE WHEN h.status = 'pending_action' THEN 'deleted' ELSE h.status END,
               COALESCE(j.operation, a.operation, 'scrape'),
               CASE WHEN {CURRENT_HISTORY_PREDICATE} THEN 1 ELSE 0 END
        FROM history_records h
        LEFT JOIN scrape_jobs j ON j.id = h.scrape_job_id
        LEFT JOIN manual_job_outcomes a ON a.history_record_id = h.id
        WHERE h.manual_job_id IS NOT NULL AND (? IS NULL OR h.executed_at < ?)
        ON CONFLICT(history_record_id) DO UPDATE SET
            manual_job_id = excluded.manual_job_id, scrape_job_id = excluded.scrape_job_id,
            file_path = excluded.file_path, status = excluded.status,
            operation = excluded.operation, counted = excluded.counted
        """,
        (cutoff, cutoff),
    )
