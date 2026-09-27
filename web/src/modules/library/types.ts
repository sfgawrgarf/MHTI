/**
 * library 域类型（文件扫描）
 */
import type { StorageLocator } from '@/shared/types/common'

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

export interface ScrapedFile {
  id: string
  source_path: string
  target_path: string | null
  file_size: number
  tmdb_id: number | null
  season: number | null
  episode: number | null
  title: string | null
  scraped_at: string
  history_record_id: string | null
}

export interface ScrapedFileListResponse {
  records: ScrapedFile[]
  total: number
}

export interface ScrapedFileCheckResponse {
  is_scraped: boolean
  record: ScrapedFile | null
}
