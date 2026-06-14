// API 类型定义

// 文件相关
export interface ScannedFile {
  filename: string
  path: string
  size: number
  extension: string
  mtime: string | null  // 修改时间 ISO 格式
  // 115 网盘等 provider 文件的标识，本地文件为 null
  file_id?: string | null
  parent_id?: string | null
}

export interface ScanRequest {
  folder_path: string
  exclude_scraped?: boolean  // 是否排除已刮削的文件，默认 true
  locator?: StorageLocator | null
}

export interface ScanResponse {
  folder_path: string
  total_files: number
  files: ScannedFile[]
  scraped_count: number  // 已刮削文件数量（被排除的）
}

export interface DirectoryEntry {
  name: string
  path: string
  is_dir: boolean
  size: number | null  // 文件大小（字节），目录为 null
  mtime: string | null  // 修改时间 ISO 格式
  provider?: StorageProvider
  file_id?: string | null
  parent_id?: string | null
  is_virtual?: boolean
}

export interface BrowseResponse {
  current_path: string
  parent_path: string | null
  entries: DirectoryEntry[]
  total: number  // 总条目数
  page: number
  page_size: number
  // 115 网盘等 provider 的目录 file_id，用于返回上级时定位父目录
  current_file_id?: string | null
  parent_file_id?: string | null
}

// 存储定位相关
export type StorageProvider = 'local' | '115'

export interface StorageLocator {
  provider: StorageProvider
  path: string
  file_id?: string | null
  parent_id?: string | null
  is_dir?: boolean
}

// 115 网盘登录相关
export interface Cloud115Status {
  enabled: boolean
  app: string
  is_logged_in: boolean
  updated_at: string | null
}

export interface Cloud115DeviceOption {
  value: string
  label: string
  group: 'standard' | 'alias'
}

export interface Cloud115QrSession {
  uid: string
  qrcode_url: string
  app: string
}

export interface Cloud115QrStatus {
  uid: string
  app: string
  status: string
  message: string
  is_logged_in: boolean
}

// 解析相关
export interface ParsedInfo {
  original_filename: string
  series_name: string | null
  season: number | null
  episode: number | null
  episode_title: string | null
  year: number | null
  is_parsed: boolean
  confidence: number
}

export interface ParseRequest {
  filename: string
  filepath: string | null
}

export interface ParseResponse {
  result: ParsedInfo
}

export interface BatchParseRequest {
  files: ParseRequest[]
}

export interface BatchParseResponse {
  results: ParsedInfo[]
  success_rate: number
}

// 通用 API 响应
export interface ApiError {
  detail: string
}

// 配置相关
export interface ApiTokenStatus {
  is_configured: boolean
  is_valid: boolean | null
  last_verified: string | null
  error_message: string | null
}

export interface ApiTokenSaveRequest {
  token: string
}

export interface ApiTokenSaveResponse {
  success: boolean
  message: string
  status: ApiTokenStatus
}

// 模板相关
export interface NamingTemplate {
  series_folder: string
  season_folder: string
  episode_file: string
}

export interface TemplatePreviewRequest {
  template: string
  sample_data?: Record<string, string | number>
}

export interface TemplatePreviewResponse {
  template: string
  preview: string
  valid: boolean
  error: string | null
}

export interface TemplateValidationResult {
  valid: boolean
  variables: string[]
  error: string | null
}

// 代理相关
export type ProxyType = 'none' | 'http' | 'socks5'

export interface ProxyConfigRequest {
  type: ProxyType
  host: string
  port: number
  username?: string | null
  password?: string | null
}

export interface ProxyConfigResponse {
  type: ProxyType
  host: string
  port: number
  has_auth: boolean
}

export interface ProxyTestResponse {
  success: boolean
  message: string
  latency_ms: number | null
}

// 语言相关
export interface LanguageConfigRequest {
  primary: string
  fallback: string[]
}

export interface LanguageConfigResponse {
  primary: string
  fallback: string[]
  supported: [string, string][]
}

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
export type TaskStatus = 'success' | 'failed' | 'timeout' | 'cancelled' | 'skipped' | 'pending_action' | 'running'
export type LogLevel = 'success' | 'warning' | 'error'
export type ConflictType = 'need_selection' | 'need_season_episode' | 'file_conflict' | 'no_match' | 'search_failed' | 'api_failed' | 'emby_conflict'

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
  error_message: string | null
  manual_job_id: number | null
  source: TaskSource
  scrape_job_id: string | null
  title: string | null
  season_number: number | null
  episode_number: number | null
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
  file_action?: 'overwrite' | 'skip' | 'rename' | null
}

// 重试刮削请求
export interface RetryRequest {
  tmdb_id: number
  season: number
  episode: number
}

export interface HistoryListResponse {
  records: HistoryRecord[]
  total: number
}

// 文件夹监控相关
export type WatcherStatus = 'idle' | 'running' | 'stopped' | 'error'
export type WatcherMode = 'realtime' | 'compat' | 'event'

export interface WatchedFolder {
  id: string
  path: string
  enabled: boolean
  mode: WatcherMode
  scan_interval_seconds: number
  file_stable_seconds: number
  auto_scrape: boolean
  last_scan: string | null
  created_at: string | null
}

export interface WatchedFolderCreate {
  path: string
  enabled?: boolean
  mode?: WatcherMode
  scan_interval_seconds?: number
  file_stable_seconds?: number
  auto_scrape?: boolean
}

export interface WatchedFolderUpdate {
  path?: string
  enabled?: boolean
  mode?: WatcherMode
  scan_interval_seconds?: number
  file_stable_seconds?: number
  auto_scrape?: boolean
}

export interface WatchedFolderListResponse {
  folders: WatchedFolder[]
  total: number
}

export interface WatcherStatusResponse {
  status: WatcherStatus
  active_watchers: number
  last_detection: string | null
  pending_files: number
}

// 整理配置相关
export type OrganizeMode = 'copy' | 'move' | 'hardlink' | 'symlink'

export interface OrganizeConfig {
  organize_dir: string
  metadata_dir: string
  organize_mode: OrganizeMode
  min_file_size_mb: number
  file_type_whitelist: string[]
  filename_blacklist: string[]
  junk_pattern_filter: string[]
  auto_clean_source: boolean
}

// 下载配置相关 - 剧集刮削器
export type ImageQuality = 'original' | 'w1280' | 'w780' | 'w500' | 'w300'

export interface DownloadConfig {
  // 剧集级别图片 (TV Show)
  series_poster: boolean  // 剧集海报
  series_backdrop: boolean  // 剧集背景图
  series_logo: boolean  // 剧集 Logo
  series_banner: boolean  // 剧集横幅
  // 季级别图片 (Season)
  season_poster: boolean  // 季海报
  // 集级别图片 (Episode)
  episode_thumb: boolean  // 剧集截图
  // 额外图片
  extra_backdrops: boolean  // 额外背景图
  extra_backdrops_count: number  // 额外背景图数量
  // 图片质量
  poster_quality: ImageQuality
  backdrop_quality: ImageQuality
  thumb_quality: ImageQuality
  // 下载行为
  overwrite_existing: boolean
}

// 监控配置相关
export interface WatcherConfig {
  enabled: boolean
  mode: WatcherMode
  performance_mode: boolean
  watch_dirs: string[]
}

// NFO 配置相关 - 剧集刮削器
export interface TVShowNfoFields {
  enabled: boolean  // 生成 tvshow.nfo
  title: boolean
  originaltitle: boolean
  sorttitle: boolean
  plot: boolean
  outline: boolean
  year: boolean
  premiered: boolean
  rating: boolean
  genre: boolean
  status: boolean
  tmdbid: boolean
}

export interface SeasonNfoFields {
  enabled: boolean  // 生成 season.nfo
  title: boolean
  plot: boolean
  year: boolean
  premiered: boolean
  seasonnumber: boolean
}

export interface EpisodeNfoFields {
  enabled: boolean  // 生成 episode.nfo
  title: boolean
  plot: boolean
  season: boolean
  episode: boolean
  aired: boolean
  rating: boolean
}

export interface NfoConfig {
  enabled: boolean  // 总开关
  tvshow: TVShowNfoFields
  season: SeasonNfoFields
  episode: EpisodeNfoFields
}

// 系统配置相关
export interface SystemConfig {
  scrape_threads: number
  task_timeout: number
  retry_count: number
  concurrent_downloads: number
}

// 手动任务相关
export type ManualJobStatus = 'pending' | 'running' | 'success' | 'failed' | 'cancelled'

export const LinkMode = {
  HARDLINK: 1,
  MOVE: 2,
  COPY: 3,
  SYMLINK: 4,
} as const

export type LinkMode = (typeof LinkMode)[keyof typeof LinkMode]

export interface ManualJob {
  id: number
  scan_path: string
  target_folder: string
  metadata_dir: string
  link_mode: LinkMode
  delete_empty_parent: boolean
  config_reuse_id: number | null
  created_at: string
  started_at: string | null
  finished_at: string | null
  status: ManualJobStatus
  success_count: number
  skip_count: number
  error_count: number
  total_count: number
  error_message: string | null
}

export interface ManualJobCreate {
  scan_path: string
  target_folder: string
  metadata_dir?: string
  scan_locator?: StorageLocator | null
  target_locator?: StorageLocator | null
  metadata_locator?: StorageLocator | null
  allow_local_output?: boolean
  link_mode?: LinkMode
  delete_empty_parent?: boolean
  config_reuse_id?: number | null
  advanced_settings?: ManualJobAdvancedSettings | null
}

// 手动任务高级设置 - 剧集刮削器
export interface ManualJobAdvancedSettings {
  // 各分类的全局配置开关
  use_global_organize: boolean
  use_global_download: boolean
  use_global_naming: boolean
  use_global_metadata: boolean
  // 整理设置（当 use_global_organize=false 时使用）
  metadata_folder: string
  delete_metadata_on_fail: boolean
  overwrite_video: boolean
  overwrite_image: boolean
  file_size_filter: number
  file_ext_whitelist: string[]
  file_name_blacklist: string[]
  file_sanitize_list: string[]
  // 自动清理
  protect_ext_whitelist: boolean
  delete_by_size: boolean
  delete_by_ext: boolean
  delete_by_name: boolean
  extra_ext_whitelist: string[]
  // 下载设置（当 use_global_download=false 时使用）
  download_poster: boolean
  download_thumb: boolean
  download_fanart: boolean
  // 命名设置（当 use_global_naming=false 时使用）
  series_folder_template: string
  season_folder_template: string
  episode_file_template: string
  // 元数据设置（当 use_global_metadata=false 时使用）
  scrape_title: boolean
  scrape_plot: boolean
  // NFO设置
  nfo_enabled: boolean
}

export interface ManualJobListResponse {
  jobs: ManualJob[]
  total: number
}

// Emby 相关
export type EmbyConflictType = 'no_conflict' | 'episode_exists' | 'series_exists'

export interface EmbyConfig {
  enabled: boolean
  server_url: string
  has_api_key: boolean
  user_id: string
  library_ids: string[]
  check_before_scrape: boolean
  timeout: number
}

export interface EmbyConfigRequest {
  enabled: boolean
  server_url: string
  api_key: string
  user_id: string
  library_ids: string[]
  check_before_scrape: boolean
  timeout: number
}

export interface EmbyLibrary {
  id: string
  name: string
  type: string
  item_count: number
}

export interface EmbyTestResponse {
  success: boolean
  message: string
  server_name: string | null
  server_version: string | null
  libraries: EmbyLibrary[]
  latency_ms: number | null
}

export interface EmbySeriesMatch {
  id: string
  name: string
  year: number | null
  path: string | null
  tmdb_id: number | null
}

export interface EmbyEpisodeMatch {
  id: string
  name: string
  season: number
  episode: number
  path: string | null
  series_id: string
  series_name: string
}

export interface EmbyConflictResult {
  conflict_type: EmbyConflictType
  message: string | null
  existing_series: EmbySeriesMatch | null
  existing_episode: EmbyEpisodeMatch | null
}

// ============================================================================
// 日志管理相关
// ============================================================================

export type SystemLogLevel = 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR' | 'CRITICAL'

export interface SystemLogEntry {
  id: number
  timestamp: string
  level: SystemLogLevel
  logger: string
  message: string
  extra_data: Record<string, unknown> | null
  request_id: string | null
  user_id: number | null
}

export interface LogListResponse {
  items: SystemLogEntry[]
  total: number
  page: number
  page_size: number
  total_pages: number
}

export interface LogStats {
  total: number
  by_level: Record<string, number>
  by_logger: Record<string, number>
  oldest_entry: string | null
  newest_entry: string | null
}

export interface LogConfig {
  log_level: SystemLogLevel
  console_enabled: boolean
  file_enabled: boolean
  db_enabled: boolean
  max_file_size_mb: number
  max_file_count: number
  db_retention_days: number
  realtime_enabled: boolean
}

export interface LogConfigUpdate {
  log_level?: SystemLogLevel
  console_enabled?: boolean
  file_enabled?: boolean
  db_enabled?: boolean
  max_file_size_mb?: number
  max_file_count?: number
  db_retention_days?: number
  realtime_enabled?: boolean
}

export interface LogQuery {
  level?: SystemLogLevel
  logger?: string
  search?: string
  start_time?: string
  end_time?: string
  page?: number
  page_size?: number
}
