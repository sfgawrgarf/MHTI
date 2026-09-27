<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { NCard } from 'naive-ui'
import { jobRuntimeApi } from '@/modules/scrape/api'
import type { JobRuntimeMetrics } from '@/modules/scrape/types'

const metrics = ref<JobRuntimeMetrics | null>(null)
const loadError = ref(false)
let refreshTimer: ReturnType<typeof setInterval> | null = null

const formatPendingAge = (seconds: number | null) => {
  if (seconds === null) return '无等待'
  if (seconds < 60) return `最长等待 ${Math.round(seconds)} 秒`
  if (seconds < 3600) return `最长等待 ${Math.round(seconds / 60)} 分钟`
  return `最长等待 ${(seconds / 3600).toFixed(1)} 小时`
}

const formatTime = (time: string) => new Date(time).toLocaleTimeString('zh-CN')

const load = async () => {
  if (document.hidden) return
  try {
    metrics.value = await jobRuntimeApi.get()
    loadError.value = false
  } catch (error) {
    loadError.value = true
    console.error('加载任务运行状态失败', error)
  }
}

onMounted(() => {
  load()
  refreshTimer = setInterval(load, 5000)
})

onUnmounted(() => {
  if (refreshTimer) clearInterval(refreshTimer)
})
</script>

<template>
  <NCard class="runtime-card" size="small" title="任务运行状态">
    <div v-if="metrics" class="runtime-grid">
      <div class="runtime-item">
        <span class="runtime-label">手动扫描</span>
        <strong>{{ metrics.manual.active_tasks }}</strong>
        / {{ metrics.manual.worker_count }} worker
        <span>{{ metrics.manual.status_counts.pending || 0 }} 等待 · {{ metrics.manual.queued_in_memory }} 已入队</span>
        <span>{{ formatPendingAge(metrics.manual.oldest_pending_seconds) }}</span>
      </div>
      <div class="runtime-item">
        <span class="runtime-label">文件刮削</span>
        <strong>{{ metrics.scrape.active_tasks }}</strong>
        / {{ metrics.scrape.concurrency_limit }} 运行
        <span>{{ metrics.scrape.status_counts.pending || 0 }} 等待 · {{ metrics.scrape.queued_in_memory }} 已入队</span>
        <span>{{ formatPendingAge(metrics.scrape.oldest_pending_seconds) }}</span>
      </div>
      <div class="runtime-item">
        <span class="runtime-label">文件 I/O</span>
        <strong>{{ metrics.file_io.active }}</strong>
        / {{ metrics.file_io.workers }} 占用
        <span>{{ metrics.file_io.waiting }} 等待</span>
      </div>
    </div>
    <div v-if="metrics" class="runtime-meta">
      <span>更新于 {{ formatTime(metrics.generated_at) }}</span>
      <span v-if="loadError" class="runtime-error">更新失败，正在显示上次快照</span>
    </div>
    <div v-else class="runtime-loading" :class="{ 'runtime-error': loadError }">
      {{ loadError ? '运行状态更新失败，稍后自动重试' : '正在读取运行状态…' }}
    </div>
  </NCard>
</template>

<style scoped>
.runtime-card {
  margin-bottom: var(--space-5);
}

.runtime-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: var(--space-4);
}

.runtime-item {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  min-width: 0;
  color: var(--text-2);
  font-size: var(--text-sm);
}

.runtime-item strong {
  color: var(--text-1);
  font-size: var(--text-xl);
  font-variant-numeric: tabular-nums;
}

.runtime-label {
  color: var(--text-1);
  font-weight: var(--weight-medium);
}

.runtime-meta,
.runtime-loading {
  margin-top: var(--space-3);
  color: var(--text-3);
  font-size: var(--text-xs);
}

.runtime-meta {
  display: flex;
  gap: var(--space-3);
}

.runtime-error {
  color: var(--warning-500);
}

@media (max-width: 767px) {
  .runtime-grid {
    grid-template-columns: 1fr;
  }
}
</style>
