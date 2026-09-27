<script setup lang="ts">
/**
 * 详情页页头：返回 + 记录身份 + 记录级操作
 *
 * 记录身份（#id / 标题 / 季集 / 状态徽章）集中在这里 —— 状态原先叠在海报上，
 * 既盖住图片，又和「处理」按钮抢海报右下的位置。处理 / 重刮 / 更多按主次排列，
 * 「处理」只在需要人工介入时出现，且是页面上唯一的主色按钮。
 */
import { NButton, NIcon, NPopconfirm } from 'naive-ui'
import { ArrowBackOutline, ConstructOutline, StopCircleOutline } from '@vicons/ionicons5'
import type { HistoryRecordDetail } from '@/modules/history/types'
import type { BadgeTone } from '@/modules/history/utils'
import RecordActions from '@/modules/history/components/RecordActions.vue'
import StatusBadge from '@/shared/components/business/StatusBadge.vue'

defineProps<{
  record: HistoryRecordDetail
  title: string
  episode: string | null
  badge: { status: BadgeTone; text: string } | null
  canHandle: boolean
  busy: boolean
  canCancel: boolean
  cancelling: boolean
}>()

const emit = defineEmits<{
  back: []
  handle: []
  rescape: []
  reorganize: []
  'delete-files': []
  cancel: []
}>()
</script>

<template>
  <header class="detail-header">
    <NButton text class="back-btn" @click="emit('back')">
      <template #icon><NIcon :component="ArrowBackOutline" /></template>
      返回列表
    </NButton>

    <div class="identity">
      <span class="identity-id">#{{ record.display_id }}</span>
      <h1 class="identity-title">{{ title }}</h1>
      <span v-if="episode" class="identity-episode">{{ episode }}</span>
      <StatusBadge
        v-if="badge"
        :status="badge.status"
        :text="badge.text"
        :pulse="record.status === 'running'"
      />
    </div>

    <div class="header-actions">
      <NPopconfirm v-if="canCancel" @positive-click="emit('cancel')">
        <template #trigger>
          <NButton type="warning" ghost size="small" :loading="cancelling">
            <template #icon><NIcon :component="StopCircleOutline" /></template>
            取消任务
          </NButton>
        </template>
        取消会等待正在执行的文件操作完成安全收尾，确定继续？
      </NPopconfirm>
      <NButton v-if="canHandle" type="primary" size="small" @click="emit('handle')">
        <template #icon><NIcon :component="ConstructOutline" /></template>
        处理
      </NButton>
      <NButton
        size="small"
        :disabled="busy"
        aria-label="按 TMDB ID 重刮"
        @click="emit('rescape')"
      >
        重刮
      </NButton>
      <RecordActions
        :record="record"
        :busy="busy"
        :hide-delete="true"
        @reorganize="emit('reorganize')"
        @delete-files="emit('delete-files')"
      />
    </div>
  </header>
</template>

<style scoped>
.detail-header {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--space-3);
}

.back-btn {
  flex-shrink: 0;
}

.identity {
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: var(--space-2);
  min-width: 0;
}

.identity-id {
  font-family: var(--font-mono);
  font-size: var(--text-sm);
  color: var(--text-3);
}

.identity-title {
  margin: 0;
  font-size: var(--text-xl);
  font-weight: var(--weight-semibold);
  line-height: var(--leading-snug);
  color: var(--text-1);
  overflow-wrap: anywhere;
}

.identity-episode {
  font-family: var(--font-mono);
  font-size: var(--text-sm);
  color: var(--text-2);
}

.header-actions {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin-left: auto;
}

@media (max-width: 768px) {
  .identity-title {
    font-size: var(--text-lg);
  }

  .header-actions {
    width: 100%;
    margin-left: 0;
  }
}
</style>
