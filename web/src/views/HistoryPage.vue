<script setup lang="ts">
import { ref, h, computed, onMounted, onUnmounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  NCard,
  NSpace,
  NButton,
  NDataTable,
  NTag,
  NIcon,
  NInput,
  NSelect,
  NPagination,
  NPopconfirm,
  useMessage,
  type DataTableColumns,
} from 'naive-ui'
import {
  DownloadOutline,
  TrashOutline,
  ArrowBackOutline,
  SearchOutline,
  RefreshOutline,
  ChevronForwardOutline,
} from '@vicons/ionicons5'
import { historyApi } from '@/api/history'
import type { HistoryRecord, TaskStatus, HistoryRecordDetail } from '@/api/types'
import ResolveConflictModal from '@/components/history/ResolveConflictModal.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import TouchCard from '@/components/common/TouchCard.vue'
import StatusBadge from '@/components/common/StatusBadge.vue'
import PageSkeleton from '@/components/common/PageSkeleton.vue'
import { useWebSocket, type WSMessage } from '@/composables/useWebSocket'
import { useMobileLayout } from '@/composables/useMobileLayout'

const route = useRoute()
const router = useRouter()
const message = useMessage()
const loading = ref(false)
const records = ref<HistoryRecord[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const search = ref('')
const statusFilter = ref<TaskStatus | null>(null)

// WebSocket 实时更新
const { registerHandler, unregisterHandler } = useWebSocket()
const { isMobile } = useMobileLayout()

// 处理弹窗相关
const showResolveModal = ref(false)
const resolveRecord = ref<HistoryRecordDetail | null>(null)
const resolveMode = ref<'resolve' | 'retry'>('resolve')
const resolveLoading = ref(false)

// 从 URL 获取 manual_job_id
const manualJobId = computed(() => {
  const id = route.query.manual_job_id
  return id ? Number(id) : null
})

// 状态筛选选项
const statusOptions = [
  { label: '全部状态', value: null },
  { label: '成功', value: 'success' },
  { label: '失败', value: 'failed' },
  { label: '处理中', value: 'running' },
  { label: '待处理', value: 'pending_action' },
  { label: '超时', value: 'timeout' },
  { label: '跳过', value: 'skipped' },
  { label: '取消', value: 'cancelled' },
]

// 加载历史记录
const loadRecords = async () => {
  loading.value = true
  try {
    const response = await historyApi.listRecords({
      manual_job_id: manualJobId.value,
      page: page.value,
      page_size: pageSize.value,
      search: search.value || undefined,
      status: statusFilter.value,
    })
    records.value = response.records
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
  loadRecords()
}

// 状态筛选
const handleStatusChange = (value: TaskStatus | null) => {
  statusFilter.value = value
  page.value = 1
  loadRecords()
}

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

// 清理所有记录
const clearAllRecords = async () => {
  try {
    const result = await historyApi.clearRecords()
    message.success(result.message)
    await loadRecords()
  } catch (error) {
    message.error('清理失败')
    console.error(error)
  }
}

const retryVisibleNoMatchesWithAI = async () => {
  const candidates = records.value.filter(record => record.status === 'pending_action').map(record => record.id)
  if (!candidates.length) {
    message.info('当前页没有待处理记录')
    return
  }
  loading.value = true
  try {
    const result = await historyApi.retryNoMatchWithAI(candidates)
    message.success(`已创建 ${result.queued_job_ids.length} 个 AI 重试任务`)
    if (result.skipped.length) message.warning(`${result.skipped.length} 条记录未满足重试条件`)
    await loadRecords()
  } catch (error) {
    message.error('AI 批量重试创建失败')
    console.error(error)
  } finally {
    loading.value = false
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

// 格式化时间
const formatTime = (time: string) => new Date(time).toLocaleString('zh-CN')

// 格式化耗时
const formatDuration = (seconds: number) => {
  if (seconds < 60) return `${seconds.toFixed(1)}s`
  return `${(seconds / 60).toFixed(1)}m`
}

// 从路径提取目录（处理 "源路径 => 目标路径" 格式，显示源文件夹完整路径）
const getFolder = (path: string) => {
  // 如果包含 =>，取源路径
  if (path.includes(' => ')) {
    path = path.split(' => ')[0] ?? path
  }
  const parts = path.split(/[/\\]/)
  parts.pop() // 移除文件名
  return parts.join('/') || '/'
}

// 触发方式（基于 source 字段）
const getTriggerType = (record: HistoryRecord) => {
  if (record.source === 'watcher') return { text: '监控', type: 'success' as const }
  return { text: '手动', type: 'info' as const }
}

// 状态标签
const statusTag = (status: TaskStatus) => {
  const map = {
    success: { type: 'success' as const, text: '成功' },
    failed: { type: 'error' as const, text: '失败' },
    timeout: { type: 'warning' as const, text: '超时' },
    cancelled: { type: 'warning' as const, text: '取消' },
    skipped: { type: 'default' as const, text: '跳过' },
    replaced: { type: 'default' as const, text: '已替代' },
    pending_action: { type: 'info' as const, text: '待处理' },
    running: { type: 'info' as const, text: '处理中' },
  }
  return map[status]
}

// 转换任务状态为徽章状态
const getStatusBadge = (status: TaskStatus): { status: 'success' | 'warning' | 'error' | 'info' | 'pending' | 'default'; text: string } => {
  const map: Record<TaskStatus, { status: 'success' | 'warning' | 'error' | 'info' | 'pending' | 'default'; text: string }> = {
    success: { status: 'success', text: '成功' },
    failed: { status: 'error', text: '失败' },
    timeout: { status: 'warning', text: '超时' },
    cancelled: { status: 'warning', text: '取消' },
    skipped: { status: 'default', text: '跳过' },
    replaced: { status: 'default', text: '已替代' },
    pending_action: { status: 'pending', text: '待处理' },
    running: { status: 'info', text: '处理中' },
  }
  return map[status] || { status: 'default', text: status }
}

// 表格列
const columns: DataTableColumns<HistoryRecord> = [
  {
    title: 'ID',
    key: 'id',
    width: 70,
    render: (row) => `#${row.display_id}`,
  },
  {
    title: '名称',
    key: 'title',
    ellipsis: { tooltip: true },
    render: (row) => row.title || '-',
  },
  {
    title: '季/集',
    key: 'season_episode',
    width: 80,
    render: (row) => {
      if (row.season_number != null && row.episode_number != null) {
        return `S${row.season_number.toString().padStart(2, '0')}E${row.episode_number.toString().padStart(2, '0')}`
      }
      return '-'
    },
  },
  {
    title: '文件夹',
    key: 'folder',
    width: 200,
    ellipsis: { tooltip: true },
    render: (row) => getFolder(row.folder_path),
  },
  {
    title: '触发',
    key: 'trigger',
    width: 70,
    render: (row) => {
      const trigger = getTriggerType(row)
      return h(NTag, { type: trigger.type, size: 'small', bordered: false }, { default: () => trigger.text })
    },
  },
  {
    title: '状态',
    key: 'status',
    width: 70,
    render: (row) => {
      const tag = statusTag(row.status)
      return h(NTag, { type: tag.type, size: 'small' }, { default: () => tag.text })
    },
  },
  {
    title: '执行时间',
    key: 'executed_at',
    width: 170,
    render: (row) => formatTime(row.executed_at),
  },
  {
    title: '耗时',
    key: 'duration',
    width: 60,
    render: (row) => formatDuration(row.duration_seconds),
  },
  {
    title: '操作',
    key: 'actions',
    width: 120,
    render: (row) =>
      h(NSpace, { size: 'small' }, {
        default: () => [
          // 待处理状态显示处理按钮（打开冲突处理弹窗）
          row.status === 'pending_action' && h(NButton, {
            size: 'small',
            type: 'primary',
            onClick: (e: Event) => {
              e.stopPropagation()
              openResolveModal(row)
            }
          }, { default: () => '处理' }),
          // 失败/超时状态显示处理按钮（直接弹窗重试）
          (row.status === 'failed' || row.status === 'timeout') && h(NButton, {
            size: 'small',
            type: 'warning',
            onClick: (e: Event) => {
              e.stopPropagation()
              openResolveModal(row)
            }
          }, { default: () => '处理' }),
          // 删除按钮
          h(NButton, {
            size: 'small',
            quaternary: true,
            type: 'error',
            onClick: (e: Event) => {
              e.stopPropagation()
              deleteRecord(row)
            }
          }, { default: () => '删除' }),
        ].filter(Boolean)
      }),
  },
]

// 打开处理弹窗
// pending_action → resolve（冲突处理）；failed/timeout → retry（重试刮削）
const openResolveModal = async (row: HistoryRecord) => {
  resolveLoading.value = true
  resolveMode.value = row.status === 'pending_action' ? 'resolve' : 'retry'
  try {
    resolveRecord.value = await historyApi.getRecord(row.id)
    showResolveModal.value = true
  } catch (error) {
    message.error('加载记录详情失败')
    console.error(error)
  } finally {
    resolveLoading.value = false
  }
}

// 处理完成回调
const onResolveSuccess = () => {
  showResolveModal.value = false
  resolveRecord.value = null
  loadRecords()
}

// 行点击跳转详情
const rowProps = (row: HistoryRecord) => ({
  style: 'cursor: pointer',
  onClick: () => router.push(`/history/${row.id}`),
})

// WebSocket 消息处理 - 使用节流避免频繁刷新
const wsHandler = (msg: WSMessage) => {
  const { type, payload } = msg

  switch (type) {
    case 'history_created': {
      // 新记录创建 - 使用增量更新，避免全量刷新
      // 只在非加载状态下处理，避免数据冲突
      if (!loading.value) {
        const newRecord = payload
        // 检查是否已存在（防止重复）
        const exists = records.value.some(r => r.id === newRecord.id)
        if (!exists) {
          // 增量更新：插入到列表开头
          records.value.unshift(newRecord)
          total.value += 1
          // 显示新记录提示
          message.success('新任务已创建')
        }
      }
      break
    }

    case 'history_updated':
      // 记录更新 - 增量更新，只更新变化的字段
      {
        const idx = records.value.findIndex(r => r.id === payload.id)
        if (idx !== -1) {
          Object.assign(records.value[idx], payload)
        }
      }
      break

    case 'history_deleted':
      // 记录删除 - 从列表中移除
      {
        const idx = records.value.findIndex(r => r.id === payload.id)
        if (idx !== -1) {
          records.value.splice(idx, 1)
          total.value = Math.max(0, total.value - 1)
        }
      }
      break

    case 'history_cleared':
      // 记录清空 - 重新加载
      loadRecords()
      break
  }
}

onMounted(() => {
  loadRecords()
  // 注册 WebSocket 消息处理器
  registerHandler(wsHandler)
})

onUnmounted(() => {
  // 注销 WebSocket 消息处理器
  unregisterHandler(wsHandler)
})

// 监听 manual_job_id 变化
watch(manualJobId, () => {
  loadRecords()
})
</script>

<template>
  <div class="history-page">
    <NCard class="main-card glass-card">
      <!-- 工具栏 -->
      <div class="toolbar">
        <div class="toolbar-left">
          <NButton v-if="manualJobId" quaternary @click="goBack">
            <template #icon><NIcon :component="ArrowBackOutline" /></template>
            返回
          </NButton>
          <NInput
            v-model:value="search"
            placeholder="搜索名称、文件夹..."
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
            class="status-select"
            @update:value="handleStatusChange"
          />
        </div>
        <div class="toolbar-right">
          <NButton type="primary" secondary @click="retryVisibleNoMatchesWithAI">
            AI 重试待处理
          </NButton>
          <NButton quaternary @click="loadRecords">
            <template #icon><NIcon :component="RefreshOutline" /></template>
          </NButton>
          <NButton @click="exportRecords">
            <template #icon><NIcon :component="DownloadOutline" /></template>
            导出
          </NButton>
          <NPopconfirm @positive-click="clearAllRecords">
            <template #trigger>
              <NButton type="error" quaternary>
                <template #icon><NIcon :component="TrashOutline" /></template>
              </NButton>
            </template>
            确定要清空所有历史记录吗？
          </NPopconfirm>
        </div>
      </div>

      <!-- 任务来源提示 -->
      <div v-if="manualJobId" class="source-hint">
        <span>手动任务 #{{ manualJobId }} 的刮削记录</span>
        <NTag size="small" type="info">{{ total }} 条</NTag>
      </div>

      <!-- 加载骨架屏 -->
      <PageSkeleton v-if="loading && records.length === 0" preset="list" :count="6" />

      <!-- 移动端卡片列表 -->
      <template v-else-if="isMobile">
        <div v-if="records.length > 0" class="mobile-record-list">
          <TouchCard
            v-for="record in records"
            :key="record.id"
            clickable
            class="record-card"
            @click="router.push(`/history/${record.id}`)"
          >
            <div class="record-card-content">
              <div class="record-header">
                <span class="record-id">#{{ record.display_id }}</span>
                <StatusBadge :status="getStatusBadge(record.status).status" :text="getStatusBadge(record.status).text" size="small" />
              </div>
              <div class="record-title">{{ record.title || '未知标题' }}</div>
              <div class="record-meta">
                <span v-if="record.season_number != null && record.episode_number != null" class="record-episode">
                  S{{ record.season_number.toString().padStart(2, '0') }}E{{ record.episode_number.toString().padStart(2, '0') }}
                </span>
                <span class="record-time">{{ formatTime(record.executed_at) }}</span>
              </div>
              <div class="record-folder">{{ getFolder(record.folder_path) }}</div>
              <div class="record-actions">
                <NTag :type="getTriggerType(record).type" size="small" :bordered="false">
                  {{ getTriggerType(record).text }}
                </NTag>
                <div class="action-buttons">
                  <NButton
                    v-if="record.status === 'pending_action'"
                    size="tiny"
                    type="primary"
                    @click.stop="openResolveModal(record)"
                  >
                    处理
                  </NButton>
                  <NButton
                    v-if="record.status === 'failed' || record.status === 'timeout'"
                    size="tiny"
                    type="warning"
                    @click.stop="openResolveModal(record)"
                  >
                    处理
                  </NButton>
                  <NButton
                    size="tiny"
                    quaternary
                    type="error"
                    @click.stop="deleteRecord(record)"
                  >
                    删除
                  </NButton>
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
          title="暂无记录"
          description="还没有任何刮削记录"
        />
      </template>

      <!-- 桌面端表格 -->
      <template v-else>
        <NDataTable
          v-if="records.length > 0"
          :columns="columns"
          :data="records"
          :loading="loading"
          :row-key="(row: HistoryRecord) => row.id"
          :row-props="rowProps"
        />
        <EmptyState
          v-else
          title="暂无记录"
          description="还没有任何刮削记录"
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

    <!-- 冲突处理弹窗 -->
    <ResolveConflictModal
      v-model:show="showResolveModal"
      :record="resolveRecord"
      :mode="resolveMode"
      @success="onResolveSuccess"
    />
  </div>
</template>

<style scoped>
.history-page {
  max-width: 1400px;
  margin: 0 auto;
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
  width: 240px;
}

.status-select {
  width: 120px;
}

/* 来源提示 */
.source-hint {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 14px;
  background: linear-gradient(135deg, rgba(99, 102, 241, 0.08) 0%, rgba(139, 92, 246, 0.08) 100%);
  border-radius: 8px;
  margin-bottom: 16px;
  font-size: 14px;
  color: var(--n-text-color-2);
}

/* 分页 */
.pagination {
  display: flex;
  justify-content: flex-end;
  margin-top: 16px;
}

/* 表格样式 */
.history-page :deep(.n-data-table) {
  border-radius: 8px;
}

.history-page :deep(.n-data-table-th) {
  font-weight: 600;
}

.history-page :deep(.n-data-table-tr:hover) {
  background: var(--n-color-hover);
}

/* 按钮样式 */
.history-page :deep(.n-button--primary-type) {
  box-shadow: 0 4px 12px rgba(99, 102, 241, 0.3);
}

.history-page :deep(.n-button--primary-type:hover) {
  box-shadow: 0 6px 16px rgba(99, 102, 241, 0.4);
  transform: translateY(-1px);
}

/* 响应式 */
@media (max-width: 768px) {
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

/* 移动端卡片列表样式 */
.mobile-record-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.record-card {
  border-radius: 12px;
}

.record-card-content {
  display: flex;
  flex-direction: column;
  gap: 8px;
  flex: 1;
}

.record-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.record-id {
  font-size: 12px;
  font-weight: 600;
  color: var(--n-text-color-3);
}

.record-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--n-text-color-1);
  line-height: 1.4;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.record-meta {
  display: flex;
  align-items: center;
  gap: 12px;
  font-size: 13px;
  color: var(--n-text-color-3);
}

.record-episode {
  font-weight: 500;
  color: var(--ios-blue);
  background: var(--ios-blue-light);
  padding: 2px 6px;
  border-radius: 4px;
}

.record-folder {
  font-size: 12px;
  color: var(--n-text-color-3);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.record-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 4px;
}

.action-buttons {
  display: flex;
  gap: 8px;
}

.chevron-icon {
  color: var(--n-text-color-3);
  font-size: 18px;
}
</style>
