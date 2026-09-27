import api from '@/shared/api/client'
import type {
  ScanRequest,
  ScanResponse,
  ScrapedFileCheckResponse,
  ScrapedFileListResponse,
} from '@/modules/library/types'

/**
 * 文件相关 API
 *
 * 目录浏览走 shared/api/fs.ts 的 fsApi.browse——该接口被 FolderBrowser
 * 等跨域基础设施消费，与原 filesApi.browse 是同一实现的重复副本，
 * 此处仅保留 library 域私有的扫描接口。
 */
export const filesApi = {
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

/** 已刮削登记 API；只管理登记，不直接删除媒体文件。 */
export const scrapedFilesApi = {
  async list(params: {
    page?: number
    page_size?: number
    search?: string
  } = {}): Promise<ScrapedFileListResponse> {
    const response = await api.get<ScrapedFileListResponse>('/scraped-files', { params })
    return response.data
  },

  async delete(ids: string[]): Promise<{ deleted: number }> {
    const response = await api.delete<{ deleted: number }>('/scraped-files', {
      params: { ids },
      paramsSerializer: () => ids.map((id) => `ids=${encodeURIComponent(id)}`).join('&'),
    })
    return response.data
  },

  async deleteByPaths(paths: string[]): Promise<{ deleted: number }> {
    const response = await api.delete<{ deleted: number }>('/scraped-files/by-paths', {
      params: { paths },
      paramsSerializer: () => paths.map((path) => `paths=${encodeURIComponent(path)}`).join('&'),
    })
    return response.data
  },

  async clear(): Promise<{ deleted: number }> {
    const response = await api.delete<{ deleted: number }>('/scraped-files/clear')
    return response.data
  },

  async check(path: string): Promise<ScrapedFileCheckResponse> {
    const response = await api.get<ScrapedFileCheckResponse>('/scraped-files/check', {
      params: { path },
    })
    return response.data
  },
}
