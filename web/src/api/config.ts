import api from './index'
import type {
  ApiTokenStatus,
  ApiTokenSaveRequest,
  ApiTokenSaveResponse,
  NamingTemplate,
  TemplatePreviewRequest,
  TemplatePreviewResponse,
  ProxyConfigRequest,
  ProxyConfigResponse,
  ProxyTestResponse,
  LanguageConfigRequest,
  LanguageConfigResponse,
  OrganizeConfig,
  DownloadConfig,
  WatcherConfig,
  NfoConfig,
  SystemConfig,
} from './types'

/**
 * 配置相关 API
 */
export const configApi = {
  /**
   * 获取命名模板配置
   */
  async getDefaultTemplate(): Promise<NamingTemplate> {
    const response = await api.get<NamingTemplate>('/templates/default')
    return response.data
  },

  /**
   * 获取当前命名模板配置
   */
  async getNamingConfig(): Promise<NamingTemplate> {
    const response = await api.get<NamingTemplate>('/config/naming')
    return response.data
  },

  /**
   * 保存命名模板配置
   */
  async saveNamingConfig(config: NamingTemplate): Promise<NamingTemplate> {
    const response = await api.put<NamingTemplate>('/config/naming', config)
    return response.data
  },

  /**
   * 预览模板
   */
  async previewTemplate(template: string, sampleData?: Record<string, string | number>): Promise<TemplatePreviewResponse> {
    const request: TemplatePreviewRequest = { template, sample_data: sampleData }
    const response = await api.post<TemplatePreviewResponse>('/templates/preview', request)
    return response.data
  },

  /**
   * 获取代理配置
   */
  async getProxyConfig(): Promise<ProxyConfigResponse> {
    const response = await api.get<ProxyConfigResponse>('/config/proxy')
    return response.data
  },

  /**
   * 保存代理配置
   */
  async saveProxyConfig(config: ProxyConfigRequest): Promise<ProxyConfigResponse> {
    const response = await api.put<ProxyConfigResponse>('/config/proxy', config)
    return response.data
  },

  /**
   * 删除代理配置
   */
  async deleteProxyConfig(): Promise<{ success: boolean; message: string }> {
    const response = await api.delete<{ success: boolean; message: string }>('/config/proxy')
    return response.data
  },

  /**
   * 测试代理连接
   */
  async testProxy(config?: ProxyConfigRequest): Promise<ProxyTestResponse> {
    const response = await api.post<ProxyTestResponse>('/config/proxy/test', config)
    return response.data
  },

  /**
   * 获取语言配置
   */
  async getLanguageConfig(): Promise<LanguageConfigResponse> {
    const response = await api.get<LanguageConfigResponse>('/config/language')
    return response.data
  },

  /**
   * 保存语言配置
   */
  async saveLanguageConfig(config: LanguageConfigRequest): Promise<LanguageConfigResponse> {
    const response = await api.put<LanguageConfigResponse>('/config/language', config)
    return response.data
  },

  // ========== API Token ==========

  /**
   * 获取 API Token 状态
   */
  async getApiTokenStatus(): Promise<ApiTokenStatus> {
    const response = await api.get<ApiTokenStatus>('/config/api-token/status')
    return response.data
  },

  /**
   * 保存 API Token
   */
  async saveApiToken(token: string): Promise<ApiTokenSaveResponse> {
    const request: ApiTokenSaveRequest = { token }
    const response = await api.post<ApiTokenSaveResponse>('/config/api-token', request)
    return response.data
  },

  /**
   * 删除 API Token
   */
  async deleteApiToken(): Promise<{ success: boolean; message: string }> {
    const response = await api.delete<{ success: boolean; message: string }>('/config/api-token')
    return response.data
  },

  // ========== Organize Config ==========

  /**
   * 获取整理配置
   */
  async getOrganizeConfig(): Promise<OrganizeConfig> {
    const response = await api.get<OrganizeConfig>('/config/organize')
    return response.data
  },

  /**
   * 保存整理配置
   */
  async saveOrganizeConfig(config: OrganizeConfig): Promise<OrganizeConfig> {
    const response = await api.put<OrganizeConfig>('/config/organize', config)
    return response.data
  },

  // ========== Download Config ==========

  /**
   * 获取下载配置
   */
  async getDownloadConfig(): Promise<DownloadConfig> {
    const response = await api.get<DownloadConfig>('/config/download')
    return response.data
  },

  /**
   * 保存下载配置
   */
  async saveDownloadConfig(config: DownloadConfig): Promise<DownloadConfig> {
    const response = await api.put<DownloadConfig>('/config/download', config)
    return response.data
  },

  // ========== Watcher Config ==========

  /**
   * 获取监控配置
   */
  async getWatcherConfig(): Promise<WatcherConfig> {
    const response = await api.get<WatcherConfig>('/config/watcher-config')
    return response.data
  },

  /**
   * 保存监控配置
   */
  async saveWatcherConfig(config: WatcherConfig): Promise<WatcherConfig> {
    const response = await api.put<WatcherConfig>('/config/watcher-config', config)
    return response.data
  },

  // ========== NFO Config ==========

  /**
   * 获取 NFO 配置
   */
  async getNfoConfig(): Promise<NfoConfig> {
    const response = await api.get<NfoConfig>('/config/nfo')
    return response.data
  },

  /**
   * 保存 NFO 配置
   */
  async saveNfoConfig(config: NfoConfig): Promise<NfoConfig> {
    const response = await api.put<NfoConfig>('/config/nfo', config)
    return response.data
  },

  // ========== System Config ==========

  async getSystemConfig(): Promise<SystemConfig> {
    const response = await api.get<SystemConfig>('/config/system')
    return response.data
  },

  async saveSystemConfig(config: SystemConfig): Promise<SystemConfig> {
    const response = await api.put<SystemConfig>('/config/system', config)
    return response.data
  },
}
