import api from './index'

// 刮削任务来源
export type ScrapeJobSource = 'manual' | 'watcher'

// 刮削任务状态
export type ScrapeJobStatus = 'pending' | 'running' | 'success' | 'failed' | 'timeout' | 'cancelled' | 'skipped' | 'replaced' | 'pending_action'

// 整理模式
export type OrganizeMode = 'copy' | 'move' | 'hardlink' | 'symlink'

// 刮削任务
export interface ScrapeJob {
  id: string
  file_path: string
  output_dir: string
  metadata_dir: string | null
  link_mode: OrganizeMode | null
  source: ScrapeJobSource
  source_id: number | null
  status: ScrapeJobStatus
  created_at: string
  started_at: string | null
  finished_at: string | null
  error_message: string | null
  history_record_id: string | null
}

// 创建刮削任务请求
export interface ScrapeJobCreate {
  file_path: string
  output_dir: string
  metadata_dir?: string | null
  link_mode?: OrganizeMode | null
  source?: ScrapeJobSource
  source_id?: number | null
}

// 刮削任务列表响应
export interface ScrapeJobListResponse {
  jobs: ScrapeJob[]
  total: number
}

/**
 * 刮削任务 API
 */
export const scrapeJobApi = {
  /**
   * 创建刮削任务
   */
  async create(data: ScrapeJobCreate): Promise<ScrapeJob> {
    const response = await api.post<ScrapeJob>('/scrape-jobs', data)
    return response.data
  },

  /**
   * 获取刮削任务列表
   */
  async list(params?: {
    page?: number
    page_size?: number
    source?: ScrapeJobSource
    source_id?: number
    status?: ScrapeJobStatus
  }): Promise<ScrapeJobListResponse> {
    const response = await api.get<ScrapeJobListResponse>('/scrape-jobs', { params })
    return response.data
  },

  /**
   * 获取刮削任务详情
   */
  async get(jobId: string): Promise<ScrapeJob | null> {
    const response = await api.get<ScrapeJob | null>(`/scrape-jobs/${jobId}`)
    return response.data
  },

  /**
   * 删除刮削任务
   */
  async delete(ids: string[]): Promise<{ deleted: number }> {
    const response = await api.delete<{ deleted: number }>('/scrape-jobs', {
      params: { ids },
      paramsSerializer: (params) => {
        const searchParams = new URLSearchParams()
        params.ids.forEach((id: string) => searchParams.append('ids', id))
        return searchParams.toString()
      },
    })
    return response.data
  },

  /** 请求安全取消任务；运行中的文件操作会先完成取消收尾。 */
  async cancel(jobId: string): Promise<JobCancelResult> {
    const response = await api.post<JobCancelResult>(
      `/scrape-jobs/${jobId}/cancel`,
      undefined,
      { timeout: 0 },
    )
    return response.data
  },

  /**
   * 批量创建刮削任务
   */
  async createBatch(jobs: ScrapeJobCreate[]): Promise<ScrapeJob[]> {
    const results: ScrapeJob[] = []
    for (const job of jobs) {
      const result = await this.create(job)
      if (result) {
        results.push(result)
      }
    }
    return results
  },
}

export interface JobCancelResult {
  job_id: string
  status: string
  cancelled: boolean
  message: string
}
