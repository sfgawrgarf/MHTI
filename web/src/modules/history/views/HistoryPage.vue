<script setup lang="ts">
import { ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { NPagination, useMessage } from 'naive-ui'
import { historyApi } from '@/modules/history/api'
import type {
  HistoryFileDeleteResponse,
  HistoryRecord,
  HistoryRecordDetail,
} from '@/modules/history/types'
import { useHistoryList } from '@/modules/history/hooks/useHistoryList'
import { useHistoryRealtime } from '@/modules/history/hooks/useHistoryRealtime'
import { useHistorySelection } from '@/modules/history/hooks/useHistorySelection'
import { useElapsedClock } from '@/modules/history/hooks/useHistoryElapsed'
import ResolveConflictModal from '@/modules/history/components/ResolveConflictModal.vue'
import HistoryBatchBar from '@/modules/history/components/HistoryBatchBar.vue'
import HistoryTable from '@/modules/history/components/HistoryTable.vue'
import RecordFilesModal from '@/modules/history/components/RecordFilesModal.vue'
import RescapeModal from '@/modules/history/components/RescapeModal.vue'
import UndoBar from '@/modules/history/components/UndoBar.vue'
import HistoryRecordCard from '@/modules/history/components/HistoryRecordCard.vue'
import HistoryToolbar from '@/modules/history/components/HistoryToolbar.vue'
import EmptyState from '@/shared/components/base/EmptyState.vue'
import PageContainer from '@/shared/components/base/PageContainer.vue'
import PageSkeleton from '@/shared/components/base/PageSkeleton.vue'
import { useMobileLayout } from '@/shared/composables/useMobileLayout'

const router = useRouter()
const message = useMessage()
const { isMobile } = useMobileLayout()

const {
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
} = useHistoryList()

// 列表中只要有处理中的记录就每秒走一格（全部落终态后自动停表，不白刷列表）
const nowTick = useElapsedClock(() => records.value.some((record) => record.status === 'running'))

// 处理弹窗相关
const showResolveModal = ref(false)
const resolveRecord = ref<HistoryRecordDetail | null>(null)
const resolveMode = ref<'resolve' | 'retry'>('resolve')

// 长任务（重刮 / 重新整理）都会跑几十秒，用 busyId 把该行按钮锁住避免重复提交
const busyId = ref<string | null>(null)

// 撤销条：只在刚删了记录时出现（物理删除不可撤销，因此不显示）
const undoMessage = ref('')
const undoing = ref(false)

// 删除文件弹窗
const showFilesModal = ref(false)
const filesRecord = ref<HistoryRecord | null>(null)

// 多选批量重试（勾选态 + 进度状态，见 useHistorySelection）
const {
  checkedKeys,
  running: batchRunning,
  percent: batchPercent,
  progressText: batchProgressText,
  manualCount: batchManualCount,
  isSelectable,
  clear: clearSelection,
  toggle: toggleChecked,
  run: runBatchRetry,
  stop: stopBatchRetry,
} = useHistorySelection({ records, reload: loadRecords })

// 重刮弹窗（可搜 TMDB 取 ID）：数据先落后端再刷新，因此弹窗提交后即关
const showRescapeModal = ref(false)
const rescapeRecord = ref<HistoryRecord | null>(null)

/** 打开重刮弹窗：只记下操作对象，ID/季/集由弹窗收集 */
const openRescapeModal = (record: HistoryRecord) => {
  rescapeRecord.value = record
  showRescapeModal.value = true
}

/** 重刮：成功记录创建纠正任务，失败记录排队重试 */
const handleRescape = async (payload: { tmdbId: number; season: number; episode: number }) => {
  const record = rescapeRecord.value
  if (!record) return
  busyId.value = record.id
  try {
    const result = await historyApi.rescrapeRecord(record, {
      tmdb_id: payload.tmdbId,
      season: payload.season,
      episode: payload.episode,
    })
    message.success(result.message || '已加入重刮队列')
  } catch (error) {
    message.error('重刮失败')
    console.error(error)
  } finally {
    busyId.value = null
    await loadRecords()
  }
}

/** 重新整理：只用已存元数据重跑整理，不请求 TMDB（换海报/改命名模板后的常见动作） */
const handleReorganize = async (record: HistoryRecord) => {
  busyId.value = record.id
  try {
    const result = await historyApi.reorganizeRecord(record.id)
    message.success(result.message || '重新整理完成')
  } catch (error) {
    message.error('重新整理失败')
    console.error(error)
  } finally {
    busyId.value = null
    await loadRecords()
  }
}

const openFilesModal = (record: HistoryRecord) => {
  filesRecord.value = record
  showFilesModal.value = true
}

/** 物理删除的回报：逐条列出失败原因，而不是笼统一句「部分失败」 */
const onFilesDeleted = (response: HistoryFileDeleteResponse) => {
  if (response.failed > 0) {
    const reasons = response.results
      .filter((item) => !item.deleted && item.reason)
      .map((item) => `${item.path}：${item.reason}`)
      .join('；')
    message.warning(`已删除 ${response.deleted} 个，${response.failed} 个未删除：${reasons}`)
  } else {
    message.success(response.message || `已删除 ${response.deleted} 个文件`)
  }
  loadRecords()
}

/** 删除单条记录：成功后给出撤销入口，而不是一句会消失的 toast */
const handleDeleteRecord = async (record: HistoryRecord) => {
  await deleteRecord(record)
  undoMessage.value = `已删除记录 #${record.display_id}`
}

const handleClearRecords = async () => {
  const result = await clearAllRecords()
  if (result && result.deleted > 0) {
    undoMessage.value = `已清空 ${result.deleted} 条记录`
  }
}

const handleUndo = async () => {
  undoing.value = true
  try {
    const result = await historyApi.undoLast()
    if (result.undone) {
      message.success(result.message)
      undoMessage.value = ''
      await loadRecords()
    } else {
      message.warning(result.message)
      undoMessage.value = ''
    }
  } catch (error) {
    message.error('撤销失败')
    console.error(error)
  } finally {
    undoing.value = false
  }
}

// 打开处理弹窗
// pending_action → resolve（冲突处理）；failed/timeout → retry（重试刮削）
const openResolveModal = async (row: HistoryRecord) => {
  resolveMode.value = row.status === 'pending_action' ? 'resolve' : 'retry'
  try {
    resolveRecord.value = await historyApi.getRecord(row.id)
    showResolveModal.value = true
  } catch (error) {
    message.error('加载记录详情失败')
    console.error(error)
  }
}

const onResolveSuccess = () => {
  showResolveModal.value = false
  loadRecords()
}

// 行点击进详情
const goToDetail = (record: HistoryRecord) => {
  router.push(`/history/${record.id}`)
}

// WebSocket 实时更新（注册/卸载成对，onMounted 内先 reload 再 register）
useHistoryRealtime({ records, total, loading, reload: loadRecords })

watch(manualJobId, () => {
  page.value = 1
  clearSelection()
  loadRecords()
})

// 翻页或改筛选后，已选 id 不再对应屏幕上的记录（批量运行中的刷新不算）
watch([page, statusFilter], () => {
  if (!batchRunning.value) clearSelection()
})
</script>

<template>
  <div class="history-page">
    <!-- 主卡片（四段式容器：数据区；弹窗区在容器外平铺） -->
    <PageContainer>
      <!-- 工具栏 -->
      <template #filters>
        <HistoryToolbar
          v-model="search"
          :status-filter="statusFilter"
          :manual-job-id="manualJobId"
          @search="handleSearch"
          @status-change="handleStatusChange"
          @back="goBack"
          @refresh="loadRecords"
          @export="exportRecords"
          @clear="handleClearRecords"
        />
      </template>

      <!-- 撤销条：紧贴工具栏，用户刚做完删除动作就在这个位置 -->
      <UndoBar
        v-if="undoMessage"
        :message="undoMessage"
        :loading="undoing"
        @undo="handleUndo"
        @close="undoMessage = ''"
      />

      <!-- 任务来源提示 -->
      <div v-if="manualJobId" class="source-hint">
        <span>手动任务 #{{ manualJobId }} 的刮削记录</span>
        <span class="tabular">{{ total }} 条</span>
      </div>

      <!-- 批量操作条：有勾选才出现，紧贴工具栏 -->
      <HistoryBatchBar
        v-if="checkedKeys.length > 0"
        :count="checkedKeys.length"
        :running="batchRunning"
        :progress-text="batchProgressText"
        :percent="batchPercent"
        :manual-count="batchManualCount"
        @retry="runBatchRetry"
        @stop="stopBatchRetry"
        @clear="clearSelection"
      />

      <!-- 加载骨架屏 -->
      <PageSkeleton v-if="loading && records.length === 0" preset="list" :count="6" />

      <!-- 移动端卡片列表 -->
      <template v-else-if="isMobile">
        <div v-if="records.length > 0" class="mobile-record-list">
          <HistoryRecordCard
            v-for="record in records"
            :key="record.id"
            :record="record"
            :busy="busyId === record.id"
            :now-tick="nowTick"
            :checked="checkedKeys.includes(record.id)"
            :selectable="isSelectable(record)"
            @click="goToDetail(record)"
            @update:checked="toggleChecked(record, $event)"
            @handle="openResolveModal(record)"
            @rescape="openRescapeModal(record)"
            @reorganize="handleReorganize(record)"
            @delete-files="openFilesModal(record)"
            @delete="handleDeleteRecord(record)"
          />
        </div>
        <EmptyState
          v-else
          title="暂无记录"
          description="还没有任何刮削记录"
        />
      </template>

      <!-- 桌面端表格 -->
      <template v-else>
        <HistoryTable
          v-if="records.length > 0"
          :records="records"
          :loading="loading"
          :busy-id="busyId"
          :now-tick="nowTick"
          :checked-keys="checkedKeys"
          :selectable="isSelectable"
          @update:checked-keys="checkedKeys = $event"
          @row-click="goToDetail"
          @handle="openResolveModal"
          @rescape="openRescapeModal"
          @reorganize="handleReorganize"
          @delete-files="openFilesModal"
          @delete="handleDeleteRecord"
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
    </PageContainer>

    <!-- 重刮弹窗（可搜 TMDB 取 ID） -->
    <RescapeModal
      v-model:show="showRescapeModal"
      :record="rescapeRecord"
      :loading="busyId === rescapeRecord?.id"
      @submit="handleRescape"
    />

    <!-- 冲突处理弹窗 -->
    <ResolveConflictModal
      v-model:show="showResolveModal"
      :record="resolveRecord"
      :mode="resolveMode"
      @success="onResolveSuccess"
    />

    <!-- 删除文件二次确认（列出具体路径） -->
    <RecordFilesModal
      v-model:show="showFilesModal"
      :record="filesRecord"
      @deleted="onFilesDeleted"
    />
  </div>
</template>

<style scoped>
.history-page {
  display: flex;
  flex-direction: column;
}

/* 任务来源提示：实心浅底，不再用渐变色块 */
.source-hint {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-2) var(--space-3);
  background: var(--bg-hover);
  border-radius: var(--radius-md);
  font-size: var(--text-sm);
  color: var(--text-2);
}

.pagination {
  display: flex;
  justify-content: flex-end;
  margin-top: var(--space-5);
}

/* 移动端卡片列表 */
.mobile-record-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}
</style>