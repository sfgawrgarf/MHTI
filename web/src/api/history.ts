import api from './index'
import type { HistoryRecordDetail, HistoryListResponse, ResolveConflictRequest, RetryRequest, ScrapeLogStep } from './types'

// 获取 API 基础 URL
const getBaseUrl = () => {
  return api.defaults.baseURL || '/api'
}

/**
 * 历史记录相关 API
 */
export const historyApi = {
  /**
   * 获取历史记录列表
   */
  async listRecords(params: {
    page?: number
    page_size?: number
    manual_job_id?: number | null
    search?: string
    status?: string | null
  } = {}): Promise<HistoryListResponse> {
    const page = params.page ?? 1
    const pageSize = params.page_size ?? 50
    const response = await api.get<HistoryListResponse>('/history', {
      params: {
        limit: pageSize,
        offset: (page - 1) * pageSize,
        manual_job_id: params.manual_job_id ?? undefined,
        search: params.search || undefined,
        status: params.status ?? undefined,
      },
    })
    return response.data
  },

  /**
   * 获取历史记录详情
   */
  async getRecord(recordId: string): Promise<HistoryRecordDetail> {
    const response = await api.get<HistoryRecordDetail>(`/history/${recordId}`)
    return response.data
  },

  /**
   * 删除历史记录
   */
  async deleteRecord(recordId: string): Promise<{ success: boolean; message: string }> {
    const response = await api.delete<{ success: boolean; message: string }>(`/history/${recordId}`)
    return response.data
  },

  /**
   * 清理历史记录
   */
  async clearRecords(beforeDays?: number): Promise<{ success: boolean; deleted: number; message: string }> {
    const params = beforeDays ? { before_days: beforeDays } : {}
    const response = await api.delete<{ success: boolean; deleted: number; message: string }>('/history', { params })
    return response.data
  },

  /**
   * 导出历史记录
   */
  async exportRecords(): Promise<string> {
    const response = await api.get<string>('/history/export', {
      responseType: 'text' as const,
    })
    return response.data
  },

  /**
   * 处理冲突记录
   */
  async resolveConflict(
    recordId: string,
    request: ResolveConflictRequest
  ): Promise<{ success: boolean; message: string; dest_path?: string }> {
    const response = await api.put<{ success: boolean; message: string; dest_path?: string }>(
      `/history/${recordId}/resolve`,
      request
    )
    return response.data
  },

  /**
   * 重试刮削记录
   */
  async retryRecord(
    recordId: string,
    request: RetryRequest
  ): Promise<{ success: boolean; message: string; dest_path?: string }> {
    const response = await api.post<{ success: boolean; message: string; dest_path?: string }>(
      `/history/${recordId}/retry`,
      request
    )
    return response.data
  },

  async retryNoMatchWithAI(recordIds: string[]): Promise<{ queued_job_ids: string[]; skipped: Array<{ id: string; reason: string }> }> {
    const response = await api.post('/history/ai-retry', { record_ids: recordIds })
    return response.data
  },

  /**
   * 订阅日志更新 (SSE)
   */
  subscribeLogStream(
    recordId: string,
    onLogs: (logs: ScrapeLogStep[]) => void,
    onError?: (error: Event) => void
  ): EventSource {
    const url = `${getBaseUrl()}/history/${recordId}/logs/stream`
    const eventSource = new EventSource(url)

    eventSource.addEventListener('logs', (event: MessageEvent) => {
      try {
        const logs = JSON.parse(event.data) as ScrapeLogStep[]
        onLogs(logs)
      } catch (e) {
        console.error('Failed to parse logs:', e)
      }
    })

    eventSource.addEventListener('ping', () => {
      // 心跳，保持连接
    })

    eventSource.onerror = (error) => {
      if (onError) {
        onError(error)
      }
    }

    return eventSource
  },
}
