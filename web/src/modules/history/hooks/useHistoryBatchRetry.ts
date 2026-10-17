import { computed, ref } from 'vue'
import { useMessage } from 'naive-ui'
import { historyApi } from '@/modules/history/api'
import type { HistoryRecord } from '@/modules/history/types'
import { getRetryMatch } from '@/modules/history/utils'

/**
 * 批量重试（列表页多选后用）
 *
 * 串行提交到持久队列，实际执行由后端 worker 的并发限制管理。
 *
 * 只提交「匹配三件套齐全」的记录：确认不了 TMDB ID/季/集就没有可重试的参数，
 * 弹窗问用户又失去了批量的意义，因此计入 skipped 并在结束时说明，绝不猜一个 ID 去跑。
 */
export function useHistoryBatchRetry(options: {
  /** 当前页记录（用于把 id 还原成记录取匹配参数） */
  records: () => HistoryRecord[]
  /** 每次重试后刷新列表（实时状态由 WebSocket 推送，这里只兜底终态） */
  reload: () => Promise<void> | void
  /** 清空选中 */
  clearSelection: () => void
}) {
  const message = useMessage()

  const running = ref(false)
  const done = ref(0)
  const total = ref(0)
  const failed = ref(0)
  const skipped = ref(0)
  /** 用户点了「停止」：当前这条提交后就不再往下发 */
  const stopRequested = ref(false)

  /** 状态上允许批量重试（与列表行的「处理」按钮同一套判定） */
  const canRetry = (record: HistoryRecord) =>
    ['timeout', 'failed', 'cancelled', 'skipped', 'deleted'].includes(record.status)

  /** 可勾选：状态可重试且缺匹配信息就无法批量跑 */
  const isSelectable = (record: HistoryRecord) => canRetry(record) && getRetryMatch(record) !== null

  /** 状态可重试但缺匹配信息（勾选框禁用，界面要说明原因） */
  const needsManualCount = (records: HistoryRecord[]) => records.filter((r) => canRetry(r) && !isSelectable(r)).length

  const progressText = computed(() => (running.value ? `批量提交中 ${done.value}/${total.value}` : ''))

  const stop = () => {
    stopRequested.value = true
  }

  /** 串行重试选中记录，返回是否跑完（被停止时为 false） */
  const run = async (ids: string[]) => {
    const byId = new Map(options.records().map((record) => [record.id, record]))
    const targets: Array<{ record: HistoryRecord; tmdbId: number; season: number; episode: number }> = []
    let missing = 0

    for (const id of ids) {
      const record = byId.get(id)
      if (!record) continue
      const match = getRetryMatch(record)
      if (!match) {
        missing += 1
        continue
      }
      targets.push({ record, ...match })
    }

    // 一条都跑不了就别进入「运行中」，否则进度条一闪而过反而像出了错
    if (targets.length === 0) {
      message.warning('选中的记录都缺少匹配信息，需要逐条处理')
      return true
    }
    if (missing > 0) {
      message.info(`${missing} 条缺少匹配信息，本次跳过，需要逐条处理`)
    }

    running.value = true
    stopRequested.value = false
    done.value = 0
    total.value = targets.length
    failed.value = 0
    skipped.value = missing

    let stopped = false
    for (const target of targets) {
      if (stopRequested.value) {
        stopped = true
        break
      }
      try {
        await historyApi.retryRecord(target.record.id, {
          tmdb_id: target.tmdbId,
          season: target.season,
          episode: target.episode,
        })
      } catch (error) {
        failed.value += 1
        console.error(error)
      }
      done.value += 1
      await options.reload()
    }

    running.value = false
    stopRequested.value = false
    options.clearSelection()

    if (stopped) {
      message.info(`已停止，已提交 ${done.value}/${total.value} 条`)
    } else if (failed.value > 0) {
      message.warning(`批量提交完成：已入队 ${done.value - failed.value} 条，失败 ${failed.value} 条`)
    } else {
      message.success(`批量提交完成：已入队 ${done.value} 条`)
    }
    return !stopped
  }

  return {
    running,
    done,
    total,
    progressText,
    isSelectable,
    needsManualCount,
    run,
    stop,
  }
}
