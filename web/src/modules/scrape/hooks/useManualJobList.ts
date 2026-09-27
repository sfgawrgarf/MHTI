import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { useMessage, type DataTableRowKey } from 'naive-ui'
import { manualJobApi } from '@/modules/scrape/api'
import type { ManualJob, ManualJobStatus } from '@/modules/scrape/types'
import { activeChildCount } from '@/modules/scrape/constants'

/**
 * 手动任务列表（ScanPage）
 *
 * 列表加载/搜索/筛选/分页/批量删除 + 3 秒轮询（仅当有运行中任务时）。
 */
export function useManualJobList() {
  const router = useRouter()
  const message = useMessage()

  const loading = ref(false)
  const jobs = ref<ManualJob[]>([])
  const total = ref(0)
  const page = ref(1)
  const pageSize = ref(20)
  const search = ref('')
  const statusFilter = ref<ManualJobStatus | null>(null)
  const checkedRowKeys = ref<DataTableRowKey[]>([])
  const showCreateModal = ref(false)
  const cancellingJobIds = ref<Set<number>>(new Set())

  let refreshTimer: ReturnType<typeof setInterval> | null = null

  // 加载数据
  const loadJobs = async () => {
    loading.value = true
    try {
      const response = await manualJobApi.list({
        page: page.value,
        page_size: pageSize.value,
        search: search.value || undefined,
        status: statusFilter.value,
      })
      jobs.value = response.jobs
      total.value = response.total
    } catch (error) {
      message.error('加载失败')
      console.error(error)
    } finally {
      loading.value = false
    }
  }

  // 搜索
  const handleSearch = () => {
    page.value = 1
    loadJobs()
  }

  // 状态筛选
  const handleStatusChange = (value: string) => {
    statusFilter.value = value === 'all' ? null : (value as ManualJobStatus)
    page.value = 1
    loadJobs()
  }

  // 分页
  const handlePageChange = (p: number) => {
    page.value = p
    loadJobs()
  }

  // 批量删除
  const handleBatchDelete = async () => {
    if (checkedRowKeys.value.length === 0) return

    try {
      await manualJobApi.delete(checkedRowKeys.value as number[])
      message.success('删除成功')
      checkedRowKeys.value = []
      loadJobs()
    } catch (error) {
      message.error('删除失败')
      console.error(error)
    }
  }

  const canCancel = (job: ManualJob) =>
    job.status === 'pending' || job.status === 'running' || activeChildCount(job) > 0

  const handleCancel = async (job: ManualJob) => {
    if (!canCancel(job) || cancellingJobIds.value.has(job.id)) return
    cancellingJobIds.value = new Set(cancellingJobIds.value).add(job.id)
    try {
      const result = await manualJobApi.cancel(job.id)
      const childText = result.cancelled_scrape_jobs
        ? `，同时取消 ${result.cancelled_scrape_jobs} 个刮削任务`
        : ''
      message.success(`任务已安全取消${childText}`)
      await loadJobs()
    } catch (error) {
      const err = error as { response?: { data?: { detail?: string } } }
      message.error(err.response?.data?.detail || '取消失败或任务已经结束')
      console.error(error)
    } finally {
      const next = new Set(cancellingJobIds.value)
      next.delete(job.id)
      cancellingJobIds.value = next
    }
  }

  // 创建任务成功
  const handleCreateSuccess = () => {
    showCreateModal.value = false
    loadJobs()
    message.success('任务已创建')
  }

  // 选中行变化
  const handleCheckedRowKeysChange = (keys: DataTableRowKey[]) => {
    checkedRowKeys.value = keys
  }

  // 跳转到历史记录
  const goToHistory = (job: ManualJob) => {
    router.push({ path: '/history', query: { manual_job_id: job.id } })
  }

  // 是否有运行中的任务
  const hasRunningJobs = computed(() => jobs.value.some(canCancel))

  onMounted(() => {
    loadJobs()
    // 定时刷新（有运行中任务时）
    refreshTimer = setInterval(() => {
      if (hasRunningJobs.value) {
        loadJobs()
      }
    }, 3000)
  })

  onUnmounted(() => {
    if (refreshTimer) {
      clearInterval(refreshTimer)
    }
  })

  return {
    loading,
    jobs,
    total,
    page,
    pageSize,
    search,
    statusFilter,
    checkedRowKeys,
    showCreateModal,
    cancellingJobIds,
    loadJobs,
    handleSearch,
    handleStatusChange,
    handlePageChange,
    handleBatchDelete,
    canCancel,
    handleCancel,
    handleCreateSuccess,
    handleCheckedRowKeysChange,
    goToHistory,
    hasRunningJobs,
  }
}
