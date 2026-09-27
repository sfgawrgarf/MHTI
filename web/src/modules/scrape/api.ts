import api from '@/shared/api/client'
import type {
  JobRuntimeMetrics,
  ManualJob,
  ManualJobCancelResult,
  ManualJobCreate,
  ManualJobListResponse,
  ManualJobStatus,
  ScrapeJob,
  ScrapeJobCancelResult,
  ScrapeJobCreate,
  ScrapeJobListResponse,
  ScrapeJobSource,
  ScrapeJobStatus,
} from '@/modules/scrape/types'

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

  /** 取消扫描并等待未完成的刮削子任务安全收尾。 */
  async cancel(id: number): Promise<ManualJobCancelResult> {
    const response = await api.post<ManualJobCancelResult>(
      `/manual-jobs/${id}/cancel`,
      undefined,
      { timeout: 0 },
    )
    return response.data
  },
}

/** 单文件刮削任务 API。 */
export const scrapeJobApi = {
  async create(data: ScrapeJobCreate): Promise<ScrapeJob> {
    const response = await api.post<ScrapeJob>('/scrape-jobs', data)
    return response.data
  },

  async list(params: {
    page?: number
    page_size?: number
    source?: ScrapeJobSource
    source_id?: number
    status?: ScrapeJobStatus
  } = {}): Promise<ScrapeJobListResponse> {
    const response = await api.get<ScrapeJobListResponse>('/scrape-jobs', { params })
    return response.data
  },

  async get(jobId: string): Promise<ScrapeJob | null> {
    const response = await api.get<ScrapeJob | null>(`/scrape-jobs/${jobId}`)
    return response.data
  },

  async delete(ids: string[]): Promise<{ deleted: number }> {
    const response = await api.delete<{ deleted: number }>('/scrape-jobs', {
      params: { ids },
      paramsSerializer: () => ids.map((id) => `ids=${encodeURIComponent(id)}`).join('&'),
    })
    return response.data
  },

  /** 请求安全取消任务；后端会先完成正在进行的文件操作收尾。 */
  async cancel(jobId: string): Promise<ScrapeJobCancelResult> {
    const response = await api.post<ScrapeJobCancelResult>(
      `/scrape-jobs/${jobId}/cancel`,
      undefined,
      { timeout: 0 },
    )
    return response.data
  },
}

/** 任务队列与文件 I/O 的运行时观测 API。 */
export const jobRuntimeApi = {
  async get(): Promise<JobRuntimeMetrics> {
    const response = await api.get<JobRuntimeMetrics>('/job-runtime')
    return response.data
  },
}
