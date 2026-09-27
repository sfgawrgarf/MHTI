/**
 * settings 域类型（Emby 集成）
 */
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

export type VersionPolicy = 'coexist' | 'prefer_best' | 'skip' | 'archive'
export type AiUsageMode = 'assist_use' | 'force_use'

export interface AiConfig {
  enabled: boolean
  usage_mode: AiUsageMode
  base_url: string
  model: string
  timeout_seconds: number
  auto_apply_threshold: number
  version_policy: VersionPolicy
  has_api_key: boolean
}

export interface AiConfigUpdate extends Omit<AiConfig, 'has_api_key'> {
  api_key?: string
}

export interface AiRecognitionCandidate {
  id: string | number
  title: string
  original_title?: string | null
  year?: number | null
  overview?: string | null
  source?: string
}

export interface AiRecognitionResult {
  title: string | null
  search_titles: string[]
  season: number | null
  episode: number | null
  selected_candidate_id: string | number | null
  confidence: number
  reason: string
  warnings: string[]
  needs_confirmation: boolean
  evidence: Record<string, unknown>
}

export interface VersionPreviewRequest {
  file_path: string
  tmdb_id: number
  season: number
  episode: number
  title?: string | null
  policy?: VersionPolicy | null
}

export interface VersionPreview {
  identity_key: string
  source_fingerprint: string
  quality_score: number
  quality_labels: string[]
  action: string
  reason: string
  existing_versions: Array<Record<string, unknown>>
}

export interface VersionRecordRequest extends VersionPreviewRequest {
  target_path?: string | null
}
