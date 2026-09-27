/**
 * 详情页记录级操作编排（重刮 / 重新整理 / 删除文件）
 *
 * 从 HistoryDetailPage 抽出：详情页与列表页共用同一套操作与话术，差别只是详情页
 * 每次操作后要重新拉一次详情。busy 用单个标志锁住全部操作，避免重刮 / 重新整理
 * 并发提交（后端同一记录跑两次会互相覆盖产物）。
 *
 * 失败不再只弹提示：结果写进 actionError，由页面渲染成可见的错误条。
 */
import { ref } from 'vue'
import { historyApi } from '@/modules/history/api'
import { scrapeJobApi } from '@/modules/scrape/api'
import type { HistoryFileDeleteResponse, HistoryRecordDetail } from '@/modules/history/types'

export function useHistoryRecordActions(
  getRecord: () => HistoryRecordDetail | null,
  reload: () => Promise<void>,
) {
  const busy = ref(false)
  const cancelling = ref(false)
  const actionError = ref('')

  const describeError = (error: unknown) => {
    const err = error as { response?: { data?: { detail?: string } }; message?: string }
    return err?.response?.data?.detail || err?.message || '操作失败'
  }

  /** 包一层：统一 busy / 错误收集 / 完成后刷新详情 */
  const run = async (task: () => Promise<void>) => {
    const record = getRecord()
    if (!record || busy.value) return
    busy.value = true
    actionError.value = ''
    try {
      await task()
    } catch (error) {
      actionError.value = describeError(error)
      console.error(error)
    } finally {
      busy.value = false
      await reload()
    }
  }

  /** 按 TMDB ID 重刮（弹窗负责二次确认） */
  const retryScrape = (payload: { tmdbId: number; season: number; episode: number }) =>
    run(async () => {
      const record = getRecord()
      if (!record) return
      await historyApi.retryRecord(record.id, {
        tmdb_id: payload.tmdbId,
        season: payload.season,
        episode: payload.episode,
      })
    })

  /** 用已存元数据重新整理 */
  const reorganize = () => run(async () => {
    const record = getRecord()
    if (!record) return
    await historyApi.reorganizeRecord(record.id)
  })

  /** 取消当前记录关联的刮削任务；后端会安全等待文件 I/O 收尾。 */
  const cancelScrape = async () => {
    const record = getRecord()
    if (!record?.scrape_job_id || cancelling.value) return
    cancelling.value = true
    actionError.value = ''
    try {
      await scrapeJobApi.cancel(record.scrape_job_id)
      await reload()
    } catch (error) {
      actionError.value = describeError(error)
      console.error(error)
    } finally {
      cancelling.value = false
    }
  }

  /** 删除文件结果：失败项要逐条说明原因，成功则静默刷新 */
  const reportFileDeletion = async (response: HistoryFileDeleteResponse) => {
    if (response.failed > 0) {
      const reasons = response.results
        .filter((item) => !item.deleted && item.reason)
        .map((item) => `${item.path}：${item.reason}`)
        .join('；')
      actionError.value = `已删除 ${response.deleted} 个，${response.failed} 个未删除：${reasons}`
    }
    await reload()
  }

  return { busy, cancelling, actionError, retryScrape, reorganize, cancelScrape, reportFileDeletion }
}
