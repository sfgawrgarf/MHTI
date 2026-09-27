import api from '@/shared/api/client'
import type {
  AiConfig,
  AiConfigUpdate,
  AiRecognitionCandidate,
  AiRecognitionResult,
  EmbyConfig,
  EmbyConfigRequest,
  EmbyTestResponse,
  VersionPreview,
  VersionPreviewRequest,
  VersionRecordRequest,
} from '@/modules/settings/types'

/**
 * Emby 相关 API
 */
export const embyApi = {
  /**
   * 获取 Emby 配置
   */
  async getConfig(): Promise<EmbyConfig> {
    const response = await api.get<EmbyConfig>('/emby/config')
    return response.data
  },

  /**
   * 保存 Emby 配置
   */
  async saveConfig(config: EmbyConfigRequest): Promise<EmbyConfig> {
    const response = await api.put<EmbyConfig>('/emby/config', config)
    return response.data
  },

  /**
   * 测试 Emby 连接
   */
  async testConnection(config?: EmbyConfigRequest): Promise<EmbyTestResponse> {
    const response = await api.post<EmbyTestResponse>('/emby/test', config || null)
    return response.data
  },
}

/** AI 识别与媒体版本 API。 */
export const aiApi = {
  async getConfig(): Promise<AiConfig> {
    return (await api.get<AiConfig>('/ai/config')).data
  },

  async saveConfig(config: AiConfigUpdate): Promise<AiConfig> {
    return (await api.put<AiConfig>('/ai/config', config)).data
  },

  async clearConfig(): Promise<{ success: boolean }> {
    return (await api.delete<{ success: boolean }>('/ai/config')).data
  },

  async recognize(
    filePath: string,
    candidates: AiRecognitionCandidate[] = [],
  ): Promise<AiRecognitionResult> {
    return (
      await api.post<AiRecognitionResult>('/ai/recognize', {
        file_path: filePath,
        candidates,
      })
    ).data
  },

  async previewVersion(request: VersionPreviewRequest): Promise<VersionPreview> {
    return (await api.post<VersionPreview>('/ai/versions/preview', request)).data
  },

  async recordVersion(request: VersionRecordRequest): Promise<VersionPreview> {
    return (await api.post<VersionPreview>('/ai/versions/record', request)).data
  },
}
