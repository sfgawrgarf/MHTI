import { ref, computed, onMounted } from 'vue'
import { historyApi, type HistoryRecord } from '@/modules/history'

export function useHomeStats() {
  const loading = ref(false)
  const records = ref<HistoryRecord[]>([])
  const totalTasks = ref(0)

  const successCount = ref(0)
  const failedCount = ref(0)
  const recentTasks = computed(() => records.value.slice(0, 10))
  const weeklyStats = ref<{ label: string; value: number }[]>([])
  let generation = 0

  async function refresh() {
    const request = ++generation
    loading.value = true
    try {
      const [historyRes, summary] = await Promise.all([
        historyApi.listRecords({ page_size: 10 }),
        historyApi.getSummary(-new Date().getTimezoneOffset()),
      ])
      if (request !== generation) return
      records.value = historyRes.records
      totalTasks.value = summary.total
      successCount.value = summary.success
      failedCount.value = summary.failed
      const labels = ['周日', '周一', '周二', '周三', '周四', '周五', '周六']
      weeklyStats.value = summary.weekly.map((day, index) => ({
        label: index === summary.weekly.length - 1 ? '今天' : labels[new Date(`${day.date}T12:00:00`).getDay()]!,
        value: day.count,
      }))
    } catch (e) {
      console.error('Failed to load home stats:', e)
    } finally {
      if (request === generation) loading.value = false
    }
  }

  onMounted(refresh)

  return {
    loading,
    totalTasks,
    successCount,
    failedCount,
    recentTasks,
    weeklyStats,
    refresh,
  }
}
