import api from '@/shared/api/client'
import type {
  HistoryFileDeleteResponse,
  HistoryFileListResponse,
  HistoryFileScope,
  HistoryActionResponse,
  HistoryListResponse,
  HistoryRecordDetail,
  ResolveConflictRequest,
  RetryRequest,
  UndoResponse,
} from '@/modules/history/types'

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
  ): Promise<HistoryActionResponse> {
    const response = await api.put<HistoryActionResponse>(
      `/history/${recordId}/resolve`,
      request
    )
    return response.data
  },

  /**
   * 列出记录的源文件与刮削产物（删除前必须让用户看到具体路径）
   */
  async listRecordFiles(recordId: string): Promise<HistoryFileListResponse> {
    const response = await api.get<HistoryFileListResponse>(`/history/${recordId}/files`)
    return response.data
  },

  /**
   * 物理删除记录的源文件 / 刮削产物（不可恢复，调用方必须二次确认）
   */
  async deleteRecordFiles(
    recordId: string,
    scope: HistoryFileScope
  ): Promise<HistoryFileDeleteResponse> {
    const response = await api.post<HistoryFileDeleteResponse>(
      `/history/${recordId}/files/delete`,
      { scope }
    )
    return response.data
  },

  /**
   * 用已存元数据重跑整理（不请求 TMDB）
   */
  async reorganizeRecord(
    recordId: string
  ): Promise<HistoryActionResponse> {
    const response = await api.post<HistoryActionResponse>(
      `/history/${recordId}/reorganize`,
      undefined,
      { timeout: 0 },
    )
    return response.data
  },

  /**
   * 撤销最近一次删除/清空记录（物理删除的文件不在撤销范围内）
   */
  async undoLast(): Promise<UndoResponse> {
    const response = await api.post<UndoResponse>('/history/undo')
    return response.data
  },

  /**
   * 重试刮削记录
   */
  async retryRecord(
    recordId: string,
    request: RetryRequest
  ): Promise<HistoryActionResponse> {
    const response = await api.post<HistoryActionResponse>(
      `/history/${recordId}/retry`,
      request,
      { timeout: 0 },
    )
    return response.data
  },
}
