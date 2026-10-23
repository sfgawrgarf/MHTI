/**
 * 跨域共享类型
 *
 * 归属判据（ARCHITECTURE §5 铁律②）：`shared/api/*` 消费的类型必须留在此处，
 * 否则 shared 将反向 import modules。其余为 ≥2 域消费的类型。
 */

// ============================================================================
// 文件 / 存储定位（library + scrape + shared/components/business）
// ============================================================================

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

export type StorageProvider = 'local' | '115'

export interface StorageLocator {
  provider: StorageProvider
  path: string
  file_id?: string | null
  parent_id?: string | null
  is_dir?: boolean
}

// ============================================================================
// 配置类 DTO（shared/api/config.ts 消费；settings 域视图亦消费）
// ============================================================================

// 115 网盘登录相关
export interface Cloud115Status {
  enabled: boolean
  app: string
  is_logged_in: boolean
  updated_at: string | null
}

export interface Cloud115Account {
  user_id: string | null
  nickname: string | null
  avatar_url: string | null
  device_count: number | null
}

export interface Cloud115Membership {
  is_vip: boolean
  /** 等级名取 115 原文（年费VIP / 全球VIP / 永久VIP），本地不做映射 */
  level_name: string | null
  expire_date: string | null
  is_forever: boolean
}

export interface Cloud115Storage {
  used_bytes: number | null
  total_bytes: number | null
  /** 直接展示 115 的格式化值（34.08TB），避免本地重算与 115 页面不一致 */
  used_text: string | null
  total_text: string | null
  used_percent: number | null
}

export interface Cloud115SessionDevice {
  name: string | null
  ip: string | null
  city: string | null
  login_at: string | null
  is_unusual: boolean
}

export interface Cloud115AccountInfo {
  is_logged_in: boolean
  /** true 有效 / false 已失效 / null 未探测（未登录或读取失败） */
  is_session_valid: boolean | null
  message: string
  account: Cloud115Account | null
  membership: Cloud115Membership | null
  storage: Cloud115Storage | null
  device: Cloud115SessionDevice | null
  checked_at: string | null
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

// TMDB Token 配置
export interface ApiTokenStatus {
  is_configured: boolean
  is_valid: boolean | null
  last_verified: string | null
  error_message: string | null
  /** R18 探测：true 已开启 / false 未开启 / null 未检测或无法判定 */
  adult_enabled: boolean | null
  adult_message: string | null
  adult_checked_at: string | null
}

export interface ApiTokenSaveRequest {
  token: string
}

export interface ApiTokenSaveResponse {
  success: boolean
  message: string
  status: ApiTokenStatus
}

// 命名模板
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

// 代理配置
export type ProxyType = 'none' | 'http' | 'socks5'

export interface ProxyConfigRequest {
  type: ProxyType
  host: string
  port: number
  username?: string | null
  password?: string | null
  clear_auth?: boolean
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

// 语言配置
export interface LanguageConfigRequest {
  primary: string
  fallback: string[]
}

export interface LanguageConfigResponse {
  primary: string
  fallback: string[]
  supported: [string, string][]
}

// 整理配置
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

// 下载配置 - 剧集刮削器
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

// 文件夹监控配置
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
  output_dir: string | null
  provider: string
  file_id: string | null
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

export interface WatcherConfig {
  enabled: boolean
  mode: WatcherMode
  performance_mode: boolean
  watch_dirs: string[]
}

// NFO 配置 - 剧集刮削器
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

// 系统配置
export interface SystemConfig {
  scrape_threads: number
  task_timeout: number
  retry_count: number
  concurrent_downloads: number
}

// ============================================================================
// TMDB（shared/api/tmdb.ts 消费；history 域视图亦消费）
// ============================================================================

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

// ============================================================================
// 日志管理（shared/api/logs.ts + settings 域 + auth 域 LogViewer）
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
