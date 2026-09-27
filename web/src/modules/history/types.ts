/**
 * history 域类型（刮削记录 / TMDB / 冲突处理）
 */

// TMDB 相关
export interface TMDBSearchResult {
  id: number
  name: string
  original_name: string | null
  first_air_date: string | null
  poster_path: string | null
  overview: string | null
  vote_average: number | null
  adult: boolean
  // 详情信息（可选）
  number_of_seasons?: number | null
  number_of_episodes?: number | null
}

export interface TMDBSearchResponse {
  query: string
  total_results: number
  results: TMDBSearchResult[]
}

export interface TMDBEpisode {
  episode_number: number
  name: string
  overview: string | null
  air_date: string | null
  vote_average: number | null
  still_path: string | null
}

export interface TMDBSeason {
  season_number: number
  name: string
  overview: string | null
  air_date: string | null
  poster_path: string | null
  episode_count: number | null
  episodes: TMDBEpisode[] | null
}

export interface TMDBSeries {
  id: number
  name: string
  original_name: string | null
  overview: string | null
  first_air_date: string | null
  vote_average: number | null
  poster_path: string | null
  backdrop_path: string | null
  genres: string[]
  status: string | null
  number_of_seasons: number | null
  number_of_episodes: number | null
  seasons: TMDBSeason[]
}

// 历史记录相关
export type TaskSource = 'manual' | 'watcher'
export type TaskStatus = 'success' | 'failed' | 'timeout' | 'cancelled' | 'skipped' | 'deleted' | 'replaced' | 'pending_action' | 'running'
export type ConflictType = 'need_selection' | 'need_season_episode' | 'file_conflict' | 'no_match' | 'search_failed' | 'api_failed' | 'emby_conflict'
export type ConflictDataMap = Record<ConflictType, Record<string, unknown>>
export type LogLevel = 'success' | 'warning' | 'error'

export interface ScrapeLogEntry {
  message: string
  level: LogLevel
}

export interface ScrapeLogStep {
  name: string
  completed: boolean
  logs: ScrapeLogEntry[]
}

export interface HistoryRecord {
  id: string
  display_id: number
  task_name: string
  folder_path: string
  executed_at: string
  status: TaskStatus
  total_files: number
  success_count: number
  failed_count: number
  duration_seconds: number
  /** 本次执行开始时间（running 期间前端据此实时计时），旧数据可能为空 */
  started_at: string | null
  error_message: string | null
  manual_job_id: number | null
  source: TaskSource
  scrape_job_id: string | null
  title: string | null
  /** 已确定的 TMDB ID（重试时直接沿用，不必重新搜索），旧记录为 null */
  tmdb_id?: number | null
  season_number: number | null
  episode_number: number | null
  /** 超时停在哪一步（仅 status='timeout' 时有值），列表页用来显示「停在 X」 */
  timeout_step?: string | null
  /** 本次执行的超时阈值（秒），取自系统设置「任务超时」 */
  timeout_seconds?: number | null
}

export interface HistoryRecordDetail extends HistoryRecord {
  // 元数据
  title: string | null
  original_title: string | null
  plot: string | null
  tags: string[]
  // 季/集信息
  season_number: number | null
  episode_number: number | null
  episode_title: string | null
  episode_overview: string | null
  episode_still_url: string | null
  episode_air_date: string | null
  // 图片
  cover_url: string | null
  poster_url: string | null
  thumb_url: string | null
  // 其他信息
  release_date: string | null
  rating: number | null
  votes: number | null
  translator: string | null
  // 刮削日志
  scrape_logs: ScrapeLogStep[]
  // 冲突处理
  conflict_type: ConflictType | null
  conflict_data: Record<string, unknown> | null
}

// 冲突处理请求
export interface ResolveConflictRequest {
  conflict_type: ConflictType
  tmdb_id?: number | null
  season?: number | null
  episode?: number | null
  file_action?: 'overwrite' | 'skip' | 'rename' | 'force' | null
  resolution_action?: 'rematch' | null
}

/** 历史操作统一响应；冲突处理可能只创建队列任务而不立即产出文件。 */
export interface HistoryActionResponse {
  success: boolean
  message: string
  queued?: boolean
  job_id?: string
  status?: string
  dest_path?: string
}

// 重试刮削请求
export interface RetryRequest {
  tmdb_id: number
  season: number
  episode: number
}

// 记录关联文件（内联操作与删除二次确认用）
/** source 源文件 / organized 整理后的视频 / metadata 刮削产出的元数据（nfo、图片） */
export type HistoryFileRole = 'source' | 'organized' | 'metadata'
export type HistoryFileScope = 'source' | 'organized' | 'all'

export interface HistoryFileEntry {
  path: string
  role: HistoryFileRole
  exists: boolean
  size: number
  /** 目录、越界路径、已不存在的文件都为 false */
  deletable: boolean
  /** 不可删除的原因，直接展示给用户 */
  reason: string | null
}

export interface HistoryFileListResponse {
  record_id: string
  folder_path: string
  files: HistoryFileEntry[]
}

export interface HistoryFileDeleteResult {
  path: string
  role: HistoryFileRole
  deleted: boolean
  reason: string | null
}

export interface HistoryFileDeleteResponse {
  success: boolean
  deleted: number
  failed: number
  results: HistoryFileDeleteResult[]
  message: string
  /** 物理删除不可撤销，后端恒为 false；前端据此不显示撤销入口 */
  undoable: boolean
}

// 撤销最近一次操作（删除记录 / 清空记录）
export interface UndoResponse {
  success: boolean
  undone: boolean
  kind: string | null
  restored: number
  message: string
}

export interface HistoryListResponse {
  records: HistoryRecord[]
  total: number
}
