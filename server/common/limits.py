"""Resource limits for externally-triggered batch and discovery work."""

# These limits protect both API requests and background jobs from retaining an
# unbounded number of paths or spawning an unbounded amount of follow-up work.
# They are intentionally high enough for normal media libraries; callers get a
# clear error instead of a silently truncated result when a limit is exceeded.
MAX_DISCOVERED_FILES = 10_000
MAX_BATCH_RENAME_ITEMS = 100
MAX_SUBTITLE_ASSOCIATE_VIDEOS = 500
MAX_EMBY_LIBRARY_IDS = 100
MAX_SCRAPE_JOB_DELETE_IDS = 500

# WebSocket clients share one authenticated administrator scope, but each
# connection still needs bounded protocol input and must only resolve actions
# for jobs it subscribed to.
MAX_WS_MESSAGE_BYTES = 64 * 1024
MAX_WS_JOB_IDS_PER_MESSAGE = 100
MAX_WS_JOB_ID_LENGTH = 128
MAX_WS_ACTION_TYPE_LENGTH = 64
MAX_WS_MESSAGE_TYPE_LENGTH = 32
MAX_WS_SUBSCRIPTIONS_PER_CLIENT = 500
