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
