import api from './index'
import type {
  ManualJob,
  ManualJobCancelResult,
  ManualJobCreate,
  ManualJobListResponse,
  ManualJobStatus,
} from './types'

/**
 * 手动任务 API
 */
export const manualJobApi = {
  /**
   * 创建手动任务
   */
  async create(data: ManualJobCreate): Promise<ManualJob> {
    const response = await api.post<ManualJob>('/manual-jobs', data)
    return response.data
  },

  /**
   * 获取任务列表
   */
  async list(params: {
    page?: number
    page_size?: number
    search?: string
    status?: ManualJobStatus | null
  } = {}): Promise<ManualJobListResponse> {
    const response = await api.get<ManualJobListResponse>('/manual-jobs', { params })
    return response.data
  },

  /**
   * 获取单个任务
   */
  async get(id: number): Promise<ManualJob> {
    const response = await api.get<ManualJob>(`/manual-jobs/${id}`)
    return response.data
  },

  /**
   * 批量删除任务
   */
  async delete(ids: number[]): Promise<{ deleted: number }> {
    const response = await api.delete<{ deleted: number }>('/manual-jobs', { data: { ids } })
    return response.data
  },

  /** 取消扫描，并等待其未完成的刮削子任务安全收尾。 */
  async cancel(id: number): Promise<ManualJobCancelResult> {
    const response = await api.post<ManualJobCancelResult>(
      `/manual-jobs/${id}/cancel`,
      undefined,
      { timeout: 0 },
    )
    return response.data
  },
}
