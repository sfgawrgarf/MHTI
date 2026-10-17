# Post-merge recovery remediation

Scope: the six defects reproduced after PR #83, based on main `5aead3f`.
Implementation is isolated in PR #85; this change does not merge or deploy it.

## File operations and recovery

Durable workers attach their job ID to filesystem operations. Receipts under
`DATA_DIR/file-operations` record source, destination, mode, phase, and file identity
(device, inode, size, modification time). `MHTI_FILE_JOURNAL_DIR` can override the
receipt directory; it must be persistent together with the application data.
Receipt JSON is atomically replaced and fsynced before publication. The staged
payload identity is saved **before** its atomic rename, covering a process exit
between publication and acknowledgement.

Recovery runs before interrupted jobs are requeued, and in a shielded worker tail
after success, failure, timeout, or cancellation. It registers a proven published
video's actual path before cleaning staging data. A fully committed interrupted
job is finalized without moving its already-consumed source again. Uncommitted
rescrape input is restored with no-clobber publication. Repeated cancellation
cannot abandon the recovery tail.

Recovery never identifies a file by basename alone. If an entry has been replaced,
a source path is occupied, a symlink still depends on a rescrape stage, or metadata
has changed externally, it retains files and receipts and marks the job/history
`pending_action` with recovery paths. An operator must inspect those paths and the
receipt before resolving that conflict. Do not delete the sole surviving staged
video or the journal as a generic cleanup step.

## Stable output name and metadata

A per-event-loop reservation selects one destination name before metadata work.
Jobs sharing a metadata series serialize preparation/publication. The explicit
reserved name is passed into the rename executor; an external writer taking that
name produces `file_conflict` rather than another filename selection.

NFO and artwork are prepared in an owned `.mhti-metadata-*` directory. Existing
metadata is seeded there to preserve skip/overwrite settings; originals are kept
for rollback. The video is published before the prepared metadata is committed.
Each metadata publication records its payload identity before rename so a partial
commit can be replayed or rolled back after process loss. Cleanup verifies the
staging directory identity. Failed/cancelled correction restores only metadata
still owned by its operation; another writer's replacement is retained.

Correction snapshots discover colocated metadata when no separate metadata root
is configured. They include episode sidecars, shared NFO, season posters, series
artwork, and extra fanart even when other episodes share them. Physical deletion
keeps its narrower shared-file eligibility rules.

## States and scheduling

Record retry/reorganize maps all actionable scraper outcomes to `pending_action`
with the same conflict enum and request data used by the ordinary worker. It
restores uncommitted staged input on those outcomes and sends a need-action event.

Backup cleanup has a separate exception boundary from scheduler dispatch. Failed
cleanup retries after 60 seconds; successful cleanup runs hourly. One undeletable
expired backup does not prevent other backups or scheduled work from proceeding.

## Validation and limits

Regression tests run in GitHub Actions only; do not run local tests or builds.
The initial tests-only commit reproduced all six defects, with the existing 754
backend tests passing. Fault injection additionally covers the publication/
acknowledgement window, each organize mode, partial metadata commit, original
metadata rollback, external replacement, concurrent naming, repeated cancellation,
and staging-directory identity.

This recovery targets application-process interruption in durable local workers.
Direct synchronous scraper APIs do not acquire a durable worker/history identity.
Reservations coordinate one application process; atomic no-clobber publication
protects against other processes taking a new target name. They do not provide a
distributed lock or a filesystem compare-and-swap against arbitrary concurrent
external edits to a target selected for explicit overwrite. Receipt persistence
is not a claim of whole-filesystem durability after power loss. Preserve the
configured data and media volumes across restarts.

## Follow-up after PR #85

The follow-up in PR #86 covers four remaining boundaries:

- Episode ownership exclusions use full paths. Another season's identically named
  video or sidecar continues to protect shared series metadata, including when
  only its NFO remains. Series discovery does not depend on a season directory
  being present, so series-only artwork/NFO is included in correction snapshots.
- Metadata replay validates an already-published target before asking for the
  staged source. Cleanup can therefore resume after partial or complete removal
  of the private metadata directory; empty plans are also forgotten. A missing
  source still fails safely when the target needs to be written.
- Publication records save staging-directory identity before creating payloads.
  COPY and cross-filesystem MOVE exclusively create the payload and persist its
  inode identity before copy2 begins. A process exit during copying leaves an
  attributable partial payload. Recovery removes it only while its recorded
  source is still available and both directory and payload ownership match.
- Metadata copies use the same staging accounting. Replay reclaims their owned
  partial payloads before retrying; rollback/cleanup also handles those receipts.
  Replaced paths, unknown directory content, or a missing media source retain
  files and receipts for inspection. Normal cleanup never scans by prefix to
  delete unknown directories.

Tests include real subprocess exits in COPY, cross-filesystem MOVE fallback, and
metadata COPY, repeated recovery after partial/complete metadata cleanup, and
physical deletion across seasons with equal filenames. All tests run in GitHub
Actions only. Older unrecorded partial directories cannot be attributed safely
from their names and require operator inspection; the new accounting applies to
operations begun after this fix.

## Separate business commitment from cleanup (PR #87)

`scrape_jobs.output_committed_at` records the successful business outcome. Ordinary
success writes it with the status transition; correction completion writes it in
the same SQLite transaction that replaces the original job/history. It is never
cleared by later display-status changes. Metadata rollback decisions use this
commit evidence as well as the uncommitted job outcome. A committed correction is
therefore never rolled back because cleanup later needs attention.

`cleanup_completed_at` and `cleanup_error` independently record receipt cleanup.
Disposable cleanup I/O errors retain the business status and receipts for retry.
Ownership conflicts still require inspection and may set `pending_action`, while
retaining the successful commit marker. Once that conflict or a legacy cleanup
failure is resolved, recovery can restore the successful status without changing
a newer job's history row. Uncommitted cancelled/failed correction still restores
its original metadata before deferred cleanup.

Migration adds the three nullable fields. It backfills legacy successful jobs and
legacy corrections whose original job points back through `replaced_by_job_id`;
that link was written atomically with successful correction completion. This also
recognizes old cleanup failures that changed the new job to `pending_action`.
Timestamp backfill uses the legacy finished/created time. Cleanup fields apply to
local receipt reconciliation; absence of a cleanup timestamp alone does not imply
a failed business operation or require remote-only jobs to have local receipts.

Receipt enumeration skips only `FileNotFoundError` when another worker removes a
JSON file between enumeration and reading. Invalid JSON, permissions, symlinks,
and identity mismatches still propagate and leave evidence intact.

Cloud validation adds an isolated COPY trial with real SQLite, durable workers,
NFO generation and file publication. It covers scrape, successful correction,
cancellation before publication, fresh service recovery, and a child process exit
after COPY publication but before metadata/final DB completion. TMDB and Emby use
fixed responses; no production credentials or media are accessed. All execution
remains in GitHub Actions. Production service updating is a separate operation.
