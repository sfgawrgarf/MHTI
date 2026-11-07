import { onMounted, onUnmounted, type Ref } from 'vue'
import type { HistoryRecord } from '@/modules/history/types'
import { useWebSocket, type WSMessage } from '@/shared/composables/useWebSocket'

/** Queries own filtering, ordering, pagination and totals; global events invalidate them. */
export function useHistoryRealtime(options: {
  records: Ref<HistoryRecord[]>
  total: Ref<number>
  loading: Ref<boolean>
  reload: () => void
}) {
  const { registerHandler, unregisterHandler } = useWebSocket()
  let refreshTimer: ReturnType<typeof setTimeout> | undefined
  const handler = (msg: WSMessage) => {
    if (!['history_created', 'history_updated', 'history_deleted', 'history_cleared', 'history_restored'].includes(msg.type)) return
    if (refreshTimer !== undefined) return
    refreshTimer = setTimeout(() => { refreshTimer = undefined; options.reload() }, 150)
  }
  onMounted(() => { registerHandler(handler); options.reload() })
  onUnmounted(() => { unregisterHandler(handler); clearTimeout(refreshTimer) })
}
