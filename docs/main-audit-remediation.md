# Main audit remediation (a2e9c109)

Scope: five independently reviewed source defects; no release or user-data changes.

- Publish staged complete output before removing a MOVE source or replacing a target.
  Failure before publication preserves both existing files. Same-filesystem moves
  stage a hardlink to retain inode identity and avoid copying large media.
  Linux uses atomic no-replace rename; other supported platforms use no-clobber
  publication. Unsupported filesystems fail safely instead of overwriting.
- Subtitle processing uses the video's mode and explicit overwrite policy,
  writes directly into its final folder, and matches only sibling subtitles.
- Local browsing, scanning, organizing and subtitle operations run in the bounded
  file-I/O executor; cancellation drains the active operation before returning.
- Refresh uses an origin-wide Web Lock and re-reads shared credentials inside
  the lock; network and lock waits are bounded. A late response cannot restore
  logged-out credentials. Environments without Web Locks currently require
  re-authentication instead of unsafe concurrent rotation. The user confirmed
  HTTPS-domain access on 2026-09-29; plain-HTTP automatic refresh is outside
  this deployment's compatibility scope. HTTPS alone does not guarantee browser
  Web Locks support; unsupported browsers retain the re-authentication fallback.
- Recursive subtitle association preserves relative paths and matches by parent
  directory as well as name.

Validation: regression tests are committed for GitHub Actions only. Do not run
local builds or tests. Coverage includes failed publication, target conflicts,
non-moving subtitle modes, nested association, scan thread isolation, cross-tab
refresh coordination, and logout during refresh.

Limits: filesystem crash durability and arbitrary external replacement of paths
are not guaranteed. If deleting the MOVE source fails after successful publication,
both copies remain and the operation reports an error rather than deleting output.
