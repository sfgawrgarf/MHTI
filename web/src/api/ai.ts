import api from './index'

export type VersionPolicy = 'coexist' | 'prefer_best' | 'skip' | 'archive'

export interface AiConfig {
  enabled: boolean
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

export interface AiRecognitionResult {
  title: string | null
  season: number | null
  episode: number | null
  selected_candidate_id: string | number | null
  confidence: number
  reason: string
  warnings: string[]
  needs_confirmation: boolean
  evidence: Record<string, unknown>
}

export const aiApi = {
  async getConfig(): Promise<AiConfig> {
    return (await api.get<AiConfig>('/ai/config')).data
  },
  async saveConfig(config: AiConfigUpdate): Promise<AiConfig> {
    return (await api.put<AiConfig>('/ai/config', config)).data
  },
  async recognize(file_path: string): Promise<AiRecognitionResult> {
    return (await api.post<AiRecognitionResult>('/ai/recognize', { file_path })).data
  },
}
