/**
 * scrape 域类型（手动任务相关）
 */
import type { StorageLocator } from '@/shared/types/common'

export type ManualJobStatus = 'pending' | 'running' | 'success' | 'failed' | 'cancelled'
export type ManualJobSource = 'manual' | 'watcher'

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
  scan_locator: StorageLocator | null
  target_locator: StorageLocator | null
  metadata_locator: StorageLocator | null
  allow_local_output: boolean
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
  source: ManualJobSource
  advanced_settings: ManualJobAdvancedSettings | null
  child_pending_count: number
  child_running_count: number
  child_pending_action_count: number
}

export interface ManualJobCancelResult {
  job_id: number
  status: ManualJobStatus
  cancelled: boolean
  cancelled_scrape_jobs: number
  message: string
}

export type ScrapeJobSource = 'manual' | 'watcher'
export type ScrapeJobStatus =
  | 'pending'
  | 'running'
  | 'success'
  | 'failed'
  | 'timeout'
  | 'cancelled'
  | 'skipped'
  | 'deleted'
  | 'replaced'
  | 'pending_action'

export type ScrapeJobLinkMode = 'copy' | 'move' | 'hardlink' | 'symlink'

export interface ScrapeJob {
  id: string
  file_path: string
  output_dir: string
  metadata_dir: string | null
  file_locator: StorageLocator | null
  output_locator: StorageLocator | null
  metadata_locator: StorageLocator | null
  allow_local_output: boolean
  link_mode: ScrapeJobLinkMode | null
  source: ScrapeJobSource
  source_id: number | null
  status: ScrapeJobStatus
  created_at: string
  started_at: string | null
  finished_at: string | null
  error_message: string | null
  history_record_id: string | null
  replaces_job_id: string | null
  replaced_by_job_id: string | null
  continuation_history_id: string | null
  correction_history_id: string | null
  correction_tmdb_id: number | null
  correction_season: number | null
  correction_episode: number | null
  file_action: string | null
  selection_log: string | null
  skip_emby_check: boolean
}

export interface ScrapeJobCreate {
  file_path: string
  output_dir: string
  metadata_dir?: string | null
  file_locator?: StorageLocator | null
  output_locator?: StorageLocator | null
  metadata_locator?: StorageLocator | null
  allow_local_output?: boolean
  link_mode?: ScrapeJobLinkMode | null
  source?: ScrapeJobSource
  source_id?: number | null
  advanced_settings?: ManualJobAdvancedSettings | null
  replaces_job_id?: string | null
  correction_history_id?: string | null
  correction_tmdb_id?: number | null
  correction_season?: number | null
  correction_episode?: number | null
  continuation_history_id?: string | null
  file_action?: string | null
  selection_log?: string | null
  skip_emby_check?: boolean
}

export interface ScrapeJobListResponse {
  jobs: ScrapeJob[]
  total: number
}

export interface ScrapeJobCancelResult {
  job_id: string
  status: ScrapeJobStatus
  cancelled: boolean
  message: string
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
  source?: ManualJobSource
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

/** 高级设置表单数据：4 个分类开关由父组件单独持有，不在表单对象内 */
export type AdvancedSettingsForm = Omit<
  ManualJobAdvancedSettings,
  'use_global_organize' | 'use_global_download' | 'use_global_naming' | 'use_global_metadata'
>

export interface ManualJobListResponse {
  jobs: ManualJob[]
  total: number
}

export interface QueueRuntimeMetrics {
  status_counts: Record<string, number>
  queued_in_memory: number
  active_tasks: number
  worker_count: number
  concurrency_limit: number
  oldest_pending_at: string | null
  oldest_pending_seconds: number | null
}

export interface FileIORuntimeMetrics {
  workers: number
  active: number
  waiting: number
}

export interface JobRuntimeMetrics {
  generated_at: string
  manual: QueueRuntimeMetrics
  scrape: QueueRuntimeMetrics
  file_io: FileIORuntimeMetrics
}
