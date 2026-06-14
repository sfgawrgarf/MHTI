import api from './index'
import type {
  BrowseResponse,
  ScanRequest,
  ScanResponse,
  StorageProvider,
} from './types'

/**
 * 文件相关 API
 */
export const filesApi = {
  /**
   * 浏览目录
   * @param path 目录路径，空字符串表示根目录
   * @param page 页码（从1开始）
   * @param pageSize 每页条目数
   * @param provider 存储提供方（local / 115），默认 local
   * @param fileId provider 文件 ID（115 用）
   */
  async browse(
    path: string = '',
    page: number = 1,
    pageSize: number = 20,
    provider?: StorageProvider,
    fileId?: string | null,
  ): Promise<BrowseResponse> {
    const response = await api.get<BrowseResponse>('/files/browse', {
      params: {
        path,
        page,
        page_size: pageSize,
        ...(provider ? { provider } : {}),
        ...(fileId ? { file_id: fileId } : {}),
      },
    })
    return response.data
  },

  /**
   * 扫描目录中的视频文件
   * @param folderPath 要扫描的目录路径
   * @param excludeScraped 是否排除已刮削的文件，默认 true
   * @param locator 存储定位信息（115 等云端目录需要）
   */
  async scan(
    folderPath: string,
    excludeScraped: boolean = true,
    locator?: ScanRequest['locator'],
  ): Promise<ScanResponse> {
    const request: ScanRequest = {
      folder_path: folderPath,
      exclude_scraped: excludeScraped,
      locator: locator ?? null,
    }
    const response = await api.post<ScanResponse>('/scan', request)
    return response.data
  },
}
