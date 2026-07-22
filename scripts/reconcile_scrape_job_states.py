#!/usr/bin/env python3
"""Reconcile scrape_jobs status from their linked history_records.

Run without --apply for a report.  The script never deletes rows; --apply only
updates linked jobs whose status differs from their history record.
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


VALID = {
    "pending",
    "running",
    "success",
    "failed",
    "timeout",
    "cancelled",
    "skipped",
    "pending_action",
    "replaced",
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=Path("data/scraper.db"))
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    with sqlite3.connect(args.database) as db:
        rows = db.execute(
            """SELECT sj.id, sj.status, hr.status, sj.history_record_id
               FROM scrape_jobs sj JOIN history_records hr
                 ON hr.id = sj.history_record_id
               WHERE sj.status != hr.status"""
        ).fetchall()
        missing = db.execute(
            """SELECT id, status FROM scrape_jobs
               WHERE history_record_id IS NULL
                  OR history_record_id NOT IN (SELECT id FROM history_records)"""
        ).fetchall()
        print(f"linked status mismatches: {len(rows)}")
        print(f"jobs without a linked history record: {len(missing)}")
        for row in rows[:20]:
            print(f"  {row[0]}: {row[1]} -> {row[2]} ({row[3]})")

        if not args.apply:
            print("dry run; use --apply after reviewing this report")
            return 0

        invalid = [row for row in rows if row[2] not in VALID]
        if invalid:
            raise SystemExit(f"refusing unknown history statuses: {invalid!r}")
        db.executemany(
            "UPDATE scrape_jobs SET status = ?, finished_at = COALESCE(finished_at, CURRENT_TIMESTAMP) WHERE id = ?",
            [(history_status, job_id) for job_id, _, history_status, _ in rows],
        )
        db.commit()
        print(f"reconciled {len(rows)} jobs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
