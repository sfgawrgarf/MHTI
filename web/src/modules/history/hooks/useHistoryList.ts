import { ref, computed, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useMessage } from 'naive-ui'
import { historyApi } from '@/modules/history/api'
import type { HistoryRecord, TaskStatus } from '@/modules/history/types'

/**
 * 刮削记录列表（HistoryPage）
 *
 * 列表加载/搜索/筛选/分页/删除/清空/导出；manual_job_id 取自路由 query。
 * 触发时机（onMounted/watch/WS handler）由页面持有。
 */
export function useHistoryList() {
  const route = useRoute()
  const router = useRouter()
  const message = useMessage()

  const loading = ref(false)
  const records = ref<HistoryRecord[]>([])
  const total = ref(0)
  const page = ref(1)
  const pageSize = ref(20)
  const search = ref('')
  const validStatuses: TaskStatus[] = ['success', 'failed', 'timeout', 'cancelled', 'skipped', 'deleted', 'replaced', 'pending_action', 'running']
  const parseStatus = (value: unknown): TaskStatus | null => validStatuses.includes(value as TaskStatus) ? value as TaskStatus : null
  const statusFilter = ref<TaskStatus | null>(parseStatus(route.query.status))
  let loadGeneration = 0

  // 从 URL 获取 manual_job_id
  const manualJobId = computed(() => {
    const id = route.query.manual_job_id
    return id ? Number(id) : null
  })

  // 加载历史记录
  const loadRecords = async () => {
    const generation = ++loadGeneration
    loading.value = true
    try {
      const response = await historyApi.listRecords({
        manual_job_id: manualJobId.value,
        page: page.value,
        page_size: pageSize.value,
        search: search.value || undefined,
        status: statusFilter.value,
      })
      if (generation !== loadGeneration) return
      records.value = response.records
      total.value = response.total
    } catch (error) {
      if (generation !== loadGeneration) return
      message.error('加载失败')
      console.error(error)
    } finally {
      if (generation === loadGeneration) loading.value = false
    }
  }

  // 搜索
  const handleSearch = () => {
    page.value = 1
    loadRecords()
  }

  // 状态筛选
  const handleStatusChange = (value: string) => {
    router.replace({ query: { ...route.query, status: value === 'all' ? undefined : value } })
  }
  watch(() => route.query.status, value => {
    statusFilter.value = parseStatus(value)
    page.value = 1
    loadRecords()
  })

  // 分页
  const handlePageChange = (p: number) => {
    page.value = p
    loadRecords()
  }

  // 返回手动任务列表
  const goBack = () => {
    router.push('/scan')
  }

  // 删除记录
  const deleteRecord = async (record: HistoryRecord) => {
    try {
      await historyApi.deleteRecord(record.id)
      message.success('记录已删除')
      await loadRecords()
    } catch (error) {
      message.error('删除失败')
      console.error(error)
    }
  }

  // 清理所有记录（回传结果，页面据此显示撤销条）
  const clearAllRecords = async () => {
    try {
      const result = await historyApi.clearRecords()
      message.success(result.message)
      await loadRecords()
      return result
    } catch (error) {
      message.error('清理失败')
      console.error(error)
      return null
    }
  }

  // 导出记录
  const exportRecords = async () => {
    try {
      const csv = await historyApi.exportRecords()
      const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' })
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = `history_${new Date().toISOString().slice(0, 10)}.csv`
      link.click()
      URL.revokeObjectURL(url)
      message.success('导出成功')
    } catch (error) {
      message.error('导出失败')
      console.error(error)
    }
  }

  return {
    loading,
    records,
    total,
    page,
    pageSize,
    search,
    statusFilter,
    manualJobId,
    loadRecords,
    handleSearch,
    handleStatusChange,
    handlePageChange,
    goBack,
    deleteRecord,
    clearAllRecords,
    exportRecords,
  }
}
