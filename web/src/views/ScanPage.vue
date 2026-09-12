<script setup lang="ts">
import { ref, h, computed, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import {
  NCard,
  NButton,
  NDataTable,
  NTag,
  NInput,
  NSelect,
  NIcon,
  NPagination,
  NPopconfirm,
  NProgress,
  useMessage,
  type DataTableColumns,
  type DataTableRowKey,
  type SelectOption,
} from 'naive-ui'
import {
  AddOutline,
  TrashOutline,
  SearchOutline,
  ListOutline,
  ChevronForwardOutline,
  StopCircleOutline,
} from '@vicons/ionicons5'
import { manualJobApi } from '@/api/manual-job'
import { jobRuntimeApi } from '@/api/job-runtime'
import type { JobRuntimeMetrics, ManualJob, ManualJobStatus } from '@/api/types'
import { LinkMode } from '@/api/types'
import TaskWizard from '@/components/scan/TaskWizard.vue'
import ProgressCell from '@/components/scan/ProgressCell.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import TouchCard from '@/components/common/TouchCard.vue'
import StatusBadge from '@/components/common/StatusBadge.vue'
import PageSkeleton from '@/components/common/PageSkeleton.vue'
import { useMobileLayout } from '@/composables/useMobileLayout'

const router = useRouter()
const message = useMessage()
const { isMobile } = useMobileLayout()
const loading = ref(false)
const jobs = ref<ManualJob[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const search = ref('')
const statusFilter = ref<ManualJobStatus | null>(null)
const checkedRowKeys = ref<DataTableRowKey[]>([])
const showCreateModal = ref(false)
const runtimeMetrics = ref<JobRuntimeMetrics | null>(null)
const runtimeMetricsLoading = ref(false)
const runtimeMetricsError = ref(false)
const cancellingJobIds = ref<Set<number>>(new Set())

let refreshTimer: ReturnType<typeof setInterval> | null = null

// 状态选项
const statusOptions = [
  { label: '全部', value: null },
  { label: '等待中', value: 'pending' },
  { label: '运行中', value: 'running' },
  { label: '成功', value: 'success' },
  { label: '失败', value: 'failed' },
  { label: '已取消', value: 'cancelled' },
] as unknown as SelectOption[]

// 整理模式映射
const linkModeMap: Record<number, { label: string; type: 'success' | 'info' | 'warning' | 'default' }> = {
  [LinkMode.HARDLINK]: { label: '硬链接', type: 'info' },
  [LinkMode.MOVE]: { label: '移动', type: 'warning' },
  [LinkMode.COPY]: { label: '复制', type: 'success' },
  [LinkMode.SYMLINK]: { label: '软链接', type: 'default' },
}

// 状态映射
const statusMap: Record<string, { label: string; type: 'success' | 'error' | 'warning' | 'info' | 'default' }> = {
  pending: { label: '等待中', type: 'default' },
  running: { label: '运行中', type: 'info' },
  success: { label: '成功', type: 'success' },
  failed: { label: '失败', type: 'error' },
  cancelled: { label: '已取消', type: 'warning' },
}

// 格式化时间
const formatTime = (time: string | null) => {
  if (!time) return '-'
  return time.replace('T', ' ').slice(0, 19)
}

// 计算用时
const formatDuration = (job: ManualJob) => {
  if (!job.started_at) return '-'
  const start = new Date(job.started_at).getTime()
  const end = job.finished_at ? new Date(job.finished_at).getTime() : Date.now()
  const seconds = (end - start) / 1000
  return `${seconds.toFixed(2)}s`
}

// 跳转到历史记录
const goToHistory = (job: ManualJob) => {
  router.push({ path: '/history', query: { manual_job_id: job.id } })
}

const activeChildCount = (job: ManualJob) =>
  job.child_pending_count + job.child_running_count + job.child_pending_action_count

const canCancel = (job: ManualJob) =>
  job.status === 'pending' || job.status === 'running' || activeChildCount(job) > 0

const handleCancel = async (job: ManualJob) => {
  cancellingJobIds.value = new Set(cancellingJobIds.value).add(job.id)
  try {
    const result = await manualJobApi.cancel(job.id)
    const childText = result.cancelled_scrape_jobs
      ? `，同时取消 ${result.cancelled_scrape_jobs} 个刮削任务`
      : ''
    message.success(`任务已安全取消${childText}`)
    await Promise.all([loadJobs(), loadRuntimeMetrics()])
  } catch (error) {
    message.error('取消失败或任务已经结束')
    console.error(error)
  } finally {
    const next = new Set(cancellingJobIds.value)
    next.delete(job.id)
    cancellingJobIds.value = next
  }
}

// 表格列
const columns: DataTableColumns<ManualJob> = [
  { type: 'selection', disabled: (row) => canCancel(row) },
  { title: '#', key: 'id', width: 60 },
  {
    title: '扫描目录',
    key: 'scan_path',
    ellipsis: { tooltip: true },
    width: 180,
  },
  {
    title: '整理目录',
    key: 'target_folder',
    ellipsis: { tooltip: true },
    width: 180,
  },
  {
    title: '整理模式',
    key: 'link_mode',
    width: 90,
    render: (row) => {
      const mode = linkModeMap[row.link_mode] || { label: '未知', type: 'default' }
      return h(NTag, { type: mode.type, size: 'small' }, { default: () => mode.label })
    },
  },
  {
    title: '创建时间',
    key: 'created_at',
    width: 160,
    render: (row) => formatTime(row.created_at),
  },
  {
    title: '用时',
    key: 'duration',
    width: 80,
    render: (row) => formatDuration(row),
  },
  {
    title: '进度',
    key: 'progress',
    width: 180,
    render: (row) =>
      h(ProgressCell, {
        successCount: row.success_count,
        skipCount: row.skip_count,
        errorCount: row.error_count,
        totalCount: row.total_count,
        childPendingCount: row.child_pending_count,
        childRunningCount: row.child_running_count,
        childPendingActionCount: row.child_pending_action_count,
      }),
  },
  {
    title: '状态',
    key: 'status',
    width: 80,
    render: (row) => {
      if (activeChildCount(row) > 0) {
        return h(NTag, { type: 'info', size: 'small' }, { default: () => '处理中' })
      }
      const status = statusMap[row.status] || { label: row.status, type: 'default' }
      return h(NTag, { type: status.type, size: 'small' }, { default: () => status.label })
    },
  },
  {
    title: '操作',
    key: 'actions',
    width: 190,
    render: (row) => h('div', { class: 'row-actions' }, [
      h(
        NButton,
        {
          size: 'small',
          quaternary: true,
          onClick: () => goToHistory(row),
        },
        {
          icon: () => h(NIcon, { component: ListOutline }),
          default: () => '记录',
        }
      ),
      canCancel(row)
        ? h(
            NPopconfirm,
            { onPositiveClick: () => handleCancel(row) },
            {
              trigger: () => h(
                NButton,
                {
                  size: 'small',
                  quaternary: true,
                  type: 'warning',
                  loading: cancellingJobIds.value.has(row.id),
                },
                {
                  icon: () => h(NIcon, { component: StopCircleOutline }),
                  default: () => '取消',
                },
              ),
              default: () => `停止任务及剩余 ${activeChildCount(row)} 个刮削任务？`,
            },
          )
        : null,
    ]),
  },
]

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

const loadRuntimeMetrics = async () => {
  if (runtimeMetricsLoading.value) return
  runtimeMetricsLoading.value = true
  try {
    runtimeMetrics.value = await jobRuntimeApi.get()
    runtimeMetricsError.value = false
  } catch (error) {
    runtimeMetricsError.value = true
    console.error('加载任务运行状态失败', error)
  } finally {
    runtimeMetricsLoading.value = false
  }
}

// 搜索
const handleSearch = () => {
  page.value = 1
  loadJobs()
}

// 状态筛选
const handleStatusChange = (value: ManualJobStatus | null) => {
  statusFilter.value = value
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

// 是否有运行中的任务
const hasRunningJobs = computed(() => jobs.value.some(canCancel))

const formatPendingAge = (seconds: number | null) => {
  if (seconds === null) return '无等待'
  if (seconds < 60) return `最长等待 ${Math.round(seconds)} 秒`
  if (seconds < 3600) return `最长等待 ${Math.round(seconds / 60)} 分钟`
  return `最长等待 ${(seconds / 3600).toFixed(1)} 小时`
}

// 状态转换为 StatusBadge 格式
const getJobStatusBadge = (job: ManualJob): { status: 'success' | 'error' | 'warning' | 'info' | 'pending' | 'default'; text: string } => {
  if (activeChildCount(job) > 0) {
    return { status: 'info', text: '处理中' }
  }
  const map: Record<ManualJobStatus, { status: 'success' | 'error' | 'warning' | 'info' | 'pending' | 'default'; text: string }> = {
    pending: { status: 'pending', text: '等待中' },
    running: { status: 'info', text: '运行中' },
    success: { status: 'success', text: '成功' },
    failed: { status: 'error', text: '失败' },
    cancelled: { status: 'warning', text: '已取消' },
  }
  return map[job.status] || { status: 'default', text: job.status }
}

// 计算进度百分比
const getProgressPercent = (job: ManualJob) => {
  if (job.total_count === 0) return 0
  return Math.round(((job.success_count + job.skip_count + job.error_count) / job.total_count) * 100)
}

const refreshVisibleData = () => {
  if (document.hidden) return
  if (hasRunningJobs.value) {
    loadJobs()
  }
  loadRuntimeMetrics()
}

const handleVisibilityChange = () => {
  if (!document.hidden) {
    loadJobs()
    loadRuntimeMetrics()
  }
}

onMounted(() => {
  loadJobs()
  loadRuntimeMetrics()
  document.addEventListener('visibilitychange', handleVisibilityChange)
  // Watchers may create tasks in the background; refresh a deduplicated snapshot.
  refreshTimer = setInterval(refreshVisibleData, 5000)
})

onUnmounted(() => {
  document.removeEventListener('visibilitychange', handleVisibilityChange)
  if (refreshTimer) {
    clearInterval(refreshTimer)
  }
})
</script>

<template>
  <div class="scan-page">
    <NCard class="runtime-card glass-card" size="small" title="任务运行状态">
      <div v-if="runtimeMetrics" class="runtime-grid">
        <div class="runtime-item">
          <span class="runtime-label">手动扫描</span>
          <strong>{{ runtimeMetrics.manual.active_tasks }}</strong>
          / {{ runtimeMetrics.manual.worker_count }} worker
          <span>{{ runtimeMetrics.manual.status_counts.pending || 0 }} 等待 · {{ runtimeMetrics.manual.queued_in_memory }} 已入队</span>
          <span>{{ formatPendingAge(runtimeMetrics.manual.oldest_pending_seconds) }}</span>
        </div>
        <div class="runtime-item">
          <span class="runtime-label">文件刮削</span>
          <strong>{{ runtimeMetrics.scrape.active_tasks }}</strong>
          / {{ runtimeMetrics.scrape.concurrency_limit }} 运行
          <span>{{ runtimeMetrics.scrape.status_counts.pending || 0 }} 等待 · {{ runtimeMetrics.scrape.queued_in_memory }} 已入队</span>
          <span>{{ formatPendingAge(runtimeMetrics.scrape.oldest_pending_seconds) }}</span>
        </div>
        <div class="runtime-item">
          <span class="runtime-label">文件 I/O</span>
          <strong>{{ runtimeMetrics.file_io.active }}</strong>
          / {{ runtimeMetrics.file_io.workers }} 占用
          <span>{{ runtimeMetrics.file_io.waiting }} 等待</span>
        </div>
      </div>
      <div v-if="runtimeMetrics" class="runtime-meta">
        <span>更新于 {{ formatTime(runtimeMetrics.generated_at) }}</span>
        <span v-if="runtimeMetricsError" class="runtime-error">更新失败，正在显示上次快照</span>
      </div>
      <div v-else class="runtime-loading" :class="{ 'runtime-error': runtimeMetricsError }">
        {{ runtimeMetricsError ? '运行状态更新失败，稍后自动重试' : '正在读取运行状态…' }}
      </div>
    </NCard>

    <!-- 主卡片 -->
    <NCard class="main-card glass-card">
      <!-- 工具栏 -->
      <div class="toolbar">
        <div class="toolbar-left">
          <NInput
            v-model:value="search"
            placeholder="搜索目录"
            clearable
            class="search-input"
            @keyup.enter="handleSearch"
          >
            <template #prefix>
              <NIcon :component="SearchOutline" />
            </template>
          </NInput>
          <NSelect
            :value="statusFilter"
            :options="statusOptions"
            placeholder="状态"
            class="status-select"
            @update:value="handleStatusChange"
          />
        </div>
        <div class="toolbar-right">
          <NButton type="primary" @click="showCreateModal = true">
            <template #icon>
              <NIcon :component="AddOutline" />
            </template>
            创建任务
          </NButton>
          <NPopconfirm @positive-click="handleBatchDelete">
            <template #trigger>
              <NButton :disabled="checkedRowKeys.length === 0" quaternary>
                <template #icon>
                  <NIcon :component="TrashOutline" />
                </template>
              </NButton>
            </template>
            确定删除选中的 {{ checkedRowKeys.length }} 条记录？
          </NPopconfirm>
        </div>
      </div>

      <!-- 加载骨架屏 -->
      <PageSkeleton v-if="loading && jobs.length === 0" preset="list" :count="5" />

      <!-- 移动端卡片列表 -->
      <template v-else-if="isMobile">
        <div v-if="jobs.length > 0" class="mobile-job-list">
          <TouchCard
            v-for="job in jobs"
            :key="job.id"
            clickable
            class="job-card"
            @click="goToHistory(job)"
          >
            <div class="job-card-content">
              <div class="job-header">
                <span class="job-id">#{{ job.id }}</span>
                <StatusBadge :status="getJobStatusBadge(job).status" :text="getJobStatusBadge(job).text" size="small" />
              </div>
              <div class="job-path">{{ job.scan_path }}</div>
              <div class="job-target">
                <span class="label">目标：</span>
                {{ job.target_folder }}
              </div>
              <div class="job-meta">
                <NTag :type="linkModeMap[job.link_mode]?.type || 'default'" size="small" :bordered="false">
                  {{ linkModeMap[job.link_mode]?.label || '未知' }}
                </NTag>
                <span class="job-time">{{ formatTime(job.created_at) }}</span>
                <NPopconfirm
                  v-if="canCancel(job)"
                  @positive-click="handleCancel(job)"
                >
                  <template #trigger>
                    <NButton
                      size="tiny"
                      quaternary
                      type="warning"
                      :loading="cancellingJobIds.has(job.id)"
                      @click.stop
                    >
                      取消
                    </NButton>
                  </template>
                  停止任务及剩余 {{ activeChildCount(job) }} 个刮削任务？
                </NPopconfirm>
              </div>
              <!-- 进度条 -->
              <div v-if="job.status === 'running' || job.total_count > 0" class="job-progress">
                <NProgress
                  type="line"
                  :percentage="getProgressPercent(job)"
                  :status="job.status === 'failed' ? 'error' : job.status === 'success' ? 'success' : 'default'"
                  :show-indicator="false"
                  :height="6"
                />
                <div class="progress-stats">
                  <span class="stat success">{{ job.success_count }}</span>
                  <span class="stat skip">{{ job.skip_count }}</span>
                  <span class="stat error">{{ job.error_count }}</span>
                  <span class="stat total">/ {{ job.total_count }}</span>
                  <span v-if="activeChildCount(job)" class="stat active">
                    后台 {{ job.child_running_count }} 运行 / {{ job.child_pending_count }} 等待 / {{ job.child_pending_action_count }} 待处理
                  </span>
                </div>
              </div>
            </div>
            <template #suffix>
              <NIcon :component="ChevronForwardOutline" class="chevron-icon" />
            </template>
          </TouchCard>
        </div>
        <EmptyState
          v-else
          title="暂无任务"
          description="创建一个新的刮削任务开始吧"
          action-text="创建任务"
          @action="showCreateModal = true"
        />
      </template>

      <!-- 桌面端表格 -->
      <template v-else>
        <NDataTable
          v-if="jobs.length > 0"
          :columns="columns"
          :data="jobs"
          :loading="loading"
          :row-key="(row: ManualJob) => row.id"
          :checked-row-keys="checkedRowKeys"
          @update:checked-row-keys="handleCheckedRowKeysChange"
        />
        <EmptyState
          v-else
          title="暂无任务"
          description="创建一个新的刮削任务开始吧"
          action-text="创建任务"
          @action="showCreateModal = true"
        />
      </template>

      <!-- 分页 -->
      <div v-if="total > pageSize" class="pagination">
        <NPagination
          v-model:page="page"
          :page-size="pageSize"
          :item-count="total"
          @update:page="handlePageChange"
        />
      </div>
    </NCard>

    <!-- 创建任务向导 -->
    <TaskWizard
      v-model:show="showCreateModal"
      @success="handleCreateSuccess"
    />
  </div>
</template>

<style scoped>
.scan-page {
  max-width: 1400px;
  margin: 0 auto;
}

.runtime-card {
  margin-bottom: 16px;
}

.runtime-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
}

.runtime-item {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 6px;
  padding: 10px 12px;
  border-radius: 10px;
  background: var(--n-color-modal);
}

.runtime-label {
  margin-right: auto;
  color: var(--n-text-color-2);
}

.runtime-item strong {
  font-size: 20px;
}

.runtime-item span:last-child,
.runtime-loading {
  color: var(--n-text-color-3);
  font-size: 12px;
}

.runtime-meta {
  display: flex;
  justify-content: flex-end;
  gap: 12px;
  margin-top: 8px;
  color: var(--n-text-color-3);
  font-size: 12px;
}

.runtime-error {
  color: var(--color-error, #d03050);
}

.row-actions {
  display: flex;
  align-items: center;
  gap: 4px;
}

/* 毛玻璃卡片 */
.glass-card {
  border: none;
  background: var(--ios-glass-bg-thick);
  backdrop-filter: blur(10px);
  -webkit-backdrop-filter: blur(10px);
}

/* 工具栏 */
.toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
  gap: 16px;
}

.toolbar-left,
.toolbar-right {
  display: flex;
  align-items: center;
  gap: 12px;
}

.search-input {
  width: 260px;
}

.status-select {
  width: 120px;
}

/* 分页 */
.pagination {
  display: flex;
  justify-content: flex-end;
  margin-top: 16px;
}

/* 表格样式 */
.scan-page :deep(.n-data-table) {
  border-radius: 8px;
}

.scan-page :deep(.n-data-table-th) {
  font-weight: 600;
}

.scan-page :deep(.n-data-table-tr:hover) {
  background: var(--n-color-hover);
}

/* 按钮样式 */
.scan-page :deep(.n-button--primary-type) {
  box-shadow: 0 4px 12px rgba(99, 102, 241, 0.3);
}

.scan-page :deep(.n-button--primary-type:hover) {
  box-shadow: 0 6px 16px rgba(99, 102, 241, 0.4);
  transform: translateY(-1px);
}

/* 响应式 */
@media (max-width: 640px) {
  .runtime-grid {
    grid-template-columns: 1fr;
  }
  .toolbar {
    flex-direction: column;
    align-items: stretch;
  }

  .toolbar-left,
  .toolbar-right {
    width: 100%;
  }

  .search-input {
    flex: 1;
  }

  .toolbar-right {
    justify-content: flex-end;
  }
}

/* 移动端任务卡片样式 */
.mobile-job-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.job-card {
  border-radius: 12px;
}

.job-card-content {
  display: flex;
  flex-direction: column;
  gap: 8px;
  flex: 1;
}

.job-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.job-id {
  font-size: 12px;
  font-weight: 600;
  color: var(--n-text-color-3);
}

.job-path {
  font-size: 15px;
  font-weight: 600;
  color: var(--n-text-color-1);
  line-height: 1.4;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.job-target {
  font-size: 13px;
  color: var(--n-text-color-2);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.job-target .label {
  color: var(--n-text-color-3);
}

.job-meta {
  display: flex;
  align-items: center;
  gap: 12px;
  font-size: 12px;
}

.job-time {
  color: var(--n-text-color-3);
}

.job-progress {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-top: 4px;
}

.progress-stats {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
}

.progress-stats .stat {
  font-weight: 500;
}

.progress-stats .stat.success {
  color: var(--ios-green);
}

.progress-stats .stat.skip {
  color: var(--ios-orange);
}

.progress-stats .stat.error {
  color: var(--ios-red);
}

.progress-stats .stat.total {
  color: var(--n-text-color-3);
}

.progress-stats .stat.active {
  margin-left: auto;
  color: var(--n-primary-color);
}

.chevron-icon {
  color: var(--n-text-color-3);
  font-size: 18px;
}
</style>
