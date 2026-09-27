<script setup lang="ts">
/**
 * 刮削记录详情页
 *
 * 数据：useHistoryRecordDetail（加载 + WS 订阅/退订 + 重连恢复）；
 * 操作：useHistoryRecordActions（重刮 / 重新整理 / 删除文件，busy 与错误统一收口）。
 * 布局：页头（DetailHeader）→ 主卡（海报 + 指标条 + 剧照条）→ 详细信息 / 刮削日志双栏。
 * 剧照并入主卡后不再单独占一张卡：页上只剩三块，主次关系靠版式而不是四张等宽卡堆叠。
 *
 * 三态齐全：加载中给骨架屏而不是整页转圈；失败给错误结果 + 重试；记录不存在给
 * 404 结果。原先只有 loading + 一个转瞬即逝的 message.error，失败后页面是空的。
 */
import { computed, ref } from 'vue'
import { NButton, NResult, NSkeleton } from 'naive-ui'
import { useHistoryRecordDetail } from '@/modules/history/hooks/useHistoryRecordDetail'
import { useHistoryRecordActions } from '@/modules/history/hooks/useHistoryRecordActions'
import { useElapsedClock } from '@/modules/history/hooks/useHistoryElapsed'
import { episodeLabel, getStatusBadge } from '@/modules/history/utils'
import DetailHeader from '@/modules/history/components/DetailHeader.vue'
import RecordMainCard from '@/modules/history/components/RecordMainCard.vue'
import RecordDetailSections from '@/modules/history/components/RecordDetailSections.vue'
import RecordLogCard from '@/modules/history/components/RecordLogCard.vue'
import RecordFilesModal from '@/modules/history/components/RecordFilesModal.vue'
import RescapeModal from '@/modules/history/components/RescapeModal.vue'
import ResolveConflictModal from '@/modules/history/components/ResolveConflictModal.vue'

const {
  loading,
  loadError,
  notFound,
  record,
  isConnected,
  needsRealtime,
  displayLogs,
  loadRecord,
  goBack,
} = useHistoryRecordDetail()

const { busy, cancelling, actionError, retryScrape, reorganize, cancelScrape, reportFileDeletion } = useHistoryRecordActions(
  () => record.value,
  () => loadRecord(false),
)

/** 处理中的耗时是活的：每秒走一格（停工时自动停表） */
const nowTick = useElapsedClock(() => record.value?.status === 'running')

// ========== 派生数据 ==========
const badge = computed(() => (record.value ? getStatusBadge(record.value.status) : null))
const episode = computed(() => (record.value ? episodeLabel(record.value) : null))
const title = computed(() => record.value?.title || record.value?.task_name || '未命名记录')

/** 是否可处理（待处理 / 失败 / 超时 / 取消） */
const canHandle = computed(() => {
  const status = record.value?.status
  return (
    status === 'pending_action' ||
    status === 'failed' ||
    status === 'timeout' ||
    status === 'cancelled'
  )
})

const canCancel = computed(() =>
  (record.value?.status === 'running' || record.value?.status === 'pending_action')
  && Boolean(record.value.scrape_job_id),
)

/** 处理弹窗模式：待处理走冲突处理，其余走重试刮削 */
const handleMode = computed<'resolve' | 'retry'>(() =>
  record.value?.status === 'pending_action' ? 'resolve' : 'retry',
)

// ========== 弹窗 ==========
const showHandleModal = ref(false)
const showFilesModal = ref(false)
const showRescapeModal = ref(false)

const handleSuccess = async () => {
  showHandleModal.value = false
  await loadRecord(false)
}
</script>

<template>
  <div class="history-detail-page">
    <!-- 加载中：骨架屏按真实版式铺位，避免整页转圈后内容跳动 -->
    <template v-if="loading && !record">
      <div class="skeleton-header">
        <NSkeleton text style="width: 120px" />
        <NSkeleton text style="width: 220px" />
      </div>
      <NSkeleton height="180px" :sharp="false" />
      <div class="info-log-row">
        <NSkeleton height="240px" :sharp="false" />
        <NSkeleton height="240px" :sharp="false" />
      </div>
    </template>

    <!-- 加载失败 / 记录不存在：给原因 + 重试，不再只弹一个提示 -->
    <NResult
      v-else-if="loadError"
      :status="notFound ? '404' : 'error'"
      :title="notFound ? '记录不存在' : '加载失败'"
      :description="loadError"
      class="state-block"
    >
      <template #footer>
        <div class="state-actions">
          <NButton v-if="!notFound" type="primary" size="small" @click="loadRecord()">重试</NButton>
          <NButton size="small" @click="goBack">返回列表</NButton>
        </div>
      </template>
    </NResult>

    <template v-else-if="record">
      <DetailHeader
        :record="record"
        :title="title"
        :episode="episode"
        :badge="badge"
        :can-handle="canHandle"
        :busy="busy || cancelling"
        :can-cancel="canCancel"
        :cancelling="cancelling"
        @back="goBack"
        @handle="showHandleModal = true"
        @rescape="showRescapeModal = true"
        @reorganize="reorganize"
        @delete-files="showFilesModal = true"
        @cancel="cancelScrape"
      />

      <!-- 操作失败提示（重刮 / 重新整理 / 删除文件共用） -->
      <div v-if="actionError" class="action-error" role="alert">
        {{ actionError }}
      </div>

      <RecordMainCard :record="record" :now-tick="nowTick" />

      <div class="info-log-row">
        <RecordDetailSections
          :record="record"
          :can-handle="canHandle"
          @handle="showHandleModal = true"
        />

        <RecordLogCard
          :logs="displayLogs"
          :is-connected="!!isConnected"
          :needs-realtime="needsRealtime"
        />
      </div>
    </template>

    <!-- 其余情况（接口没返回记录也没报错，例如记录被并发删除） -->
    <NResult
      v-else
      status="404"
      title="记录不存在"
      description="该刮削记录可能已被删除"
      class="state-block"
    >
      <template #footer>
        <NButton size="small" @click="goBack">返回列表</NButton>
      </template>
    </NResult>

    <!-- 删除文件二次确认（列出具体路径） -->
    <RecordFilesModal v-model:show="showFilesModal" :record="record" @deleted="reportFileDeletion" />

    <!-- 重刮弹窗（可搜 TMDB 取 ID） -->
    <RescapeModal
      v-model:show="showRescapeModal"
      :record="record"
      :loading="busy || cancelling"
      @submit="retryScrape"
    />

    <!-- 处理弹窗（冲突处理 / 重试刮削） -->
    <ResolveConflictModal
      v-model:show="showHandleModal"
      :record="record"
      :mode="handleMode"
      @success="handleSuccess"
    />
  </div>
</template>

<style scoped>
.history-detail-page {
  display: flex;
  flex-direction: column;
  gap: var(--space-5);
}

.skeleton-header {
  display: flex;
  align-items: center;
  gap: var(--space-4);
}

.action-error {
  padding: var(--space-2) var(--space-3);
  border-left: 2px solid var(--danger-500);
  font-size: var(--text-sm);
  color: var(--danger-500);
  overflow-wrap: anywhere;
}

.state-block {
  padding: var(--space-6) 0;
}

.state-actions {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-2);
}

.info-log-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-4);
}

@media (max-width: 768px) {
  .info-log-row {
    grid-template-columns: 1fr;
  }
}
</style>
