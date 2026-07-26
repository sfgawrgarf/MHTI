<script setup lang="ts">
/**
 * 日志查看器组件 - 用于弹窗内展示日志
 */
import { ref, computed, onMounted, onUnmounted } from 'vue'
import {
  NSpace,
  NButton,
  NSelect,
  NInput,
  NTag,
  NScrollbar,
  NEmpty,
  NSpin,
  NPopconfirm,
  NText,
  useMessage,
  type SelectOption,
} from 'naive-ui'
import * as logsApi from '@/api/logs'
import type { SystemLogEntry, SystemLogLevel, LogStats } from '@/api/types'

const message = useMessage()

// 日志列表状态
const loading = ref(false)
const logs = ref<SystemLogEntry[]>([])
const total = ref(0)

// 筛选状态
const filterLevel = ref<SystemLogLevel | null>(null)
const filterSearch = ref('')

// 统计状态
const stats = ref<LogStats | null>(null)

// 操作状态
const clearing = ref(false)

// 日志级别选项（只显示会记录的级别）
const levelOptions = [
  { label: '全部', value: null },
  { label: 'WARNING', value: 'WARNING' },
  { label: 'ERROR', value: 'ERROR' },
  { label: 'CRITICAL', value: 'CRITICAL' },
 ] as unknown as SelectOption[]

// 日志级别颜色
const levelColor = (level: SystemLogLevel) => {
  const colorMap: Record<SystemLogLevel, string> = {
    DEBUG: '#909399',
    INFO: '#007AFF',
    WARNING: '#E6A23C',
    ERROR: '#F56C6C',
    CRITICAL: '#F56C6C',
  }
  return colorMap[level]
}

// 格式化时间（只显示时:分:秒）
const formatTime = (timestamp: string) => {
  const d = new Date(timestamp)
  return d.toLocaleTimeString('zh-CN', { hour12: false })
}

// 加载日志列表（加载更多条以便滚动查看）
const loadLogs = async () => {
  loading.value = true
  try {
    const response = await logsApi.getLogs({
      level: filterLevel.value || undefined,
      search: filterSearch.value || undefined,
      page: 1,
      page_size: 100, // 加载 100 条
    })
    logs.value = response.items
    total.value = response.total
  } catch (error) {
    console.error('加载日志失败:', error)
  } finally {
    loading.value = false
  }
}

// 加载统计信息
const loadStats = async () => {
  try {
    stats.value = await logsApi.getLogStats()
  } catch (error) {
    console.error('加载统计失败:', error)
  }
}

// 刷新
const refresh = () => {
  loadLogs()
  loadStats()
}

// 清空所有日志
const clearAll = async () => {
  clearing.value = true
  try {
    const result = await logsApi.clearLogs()
    message.success(result.message || '日志已清空')
    refresh()
  } catch (error) {
    message.error('清理失败')
    console.error('清理失败:', error)
  } finally {
    clearing.value = false
  }
}

// 筛选变化
const handleFilterChange = () => {
  loadLogs()
}

// 统计显示
const errorCount = computed(() => stats.value?.by_level?.ERROR || 0)
const warningCount = computed(() => stats.value?.by_level?.WARNING || 0)

// 自动刷新
let refreshInterval: number | null = null

onMounted(() => {
  loadLogs()
  loadStats()
  // 每 30 秒刷新
  refreshInterval = window.setInterval(() => {
    if (!loading.value) loadLogs()
  }, 30000)
})

onUnmounted(() => {
  if (refreshInterval) clearInterval(refreshInterval)
})
</script>

<template>
  <div class="log-viewer">
    <!-- 头部：统计 + 操作 -->
    <div class="log-header">
      <div class="log-stats">
        <NTag v-if="errorCount > 0" type="error" size="small" round>
          {{ errorCount }} 错误
        </NTag>
        <NTag v-if="warningCount > 0" type="warning" size="small" round>
          {{ warningCount }} 警告
        </NTag>
        <NText v-if="total > 0" depth="3" style="font-size: 12px">
          共 {{ total }} 条
        </NText>
      </div>

      <NSpace size="small">
        <NButton size="tiny" quaternary @click="refresh">
          刷新
        </NButton>
        <NPopconfirm :to="false" placement="bottom" @positive-click="clearAll">
          <template #trigger>
            <NButton size="tiny" quaternary type="error" :loading="clearing" :disabled="total === 0">
              清空
            </NButton>
          </template>
          确定清空所有日志？
        </NPopconfirm>
      </NSpace>
    </div>

    <!-- 筛选栏 -->
    <div class="log-filter">
      <NSelect
        v-model:value="filterLevel"
        :options="levelOptions"
        size="tiny"
        style="width: 100px"
        @update:value="handleFilterChange"
      />
      <NInput
        v-model:value="filterSearch"
        placeholder="搜索日志..."
        size="tiny"
        clearable
        style="flex: 1"
        @keyup.enter="handleFilterChange"
        @clear="handleFilterChange"
      />
    </div>

    <!-- 日志列表 -->
    <NSpin :show="loading" size="small">
      <NScrollbar style="max-height: 280px" trigger="none">
        <div v-if="logs.length === 0 && !loading" class="log-empty">
          <NEmpty size="small" description="暂无日志记录" />
        </div>

        <div v-else class="log-list">
          <div
            v-for="log in logs"
            :key="log.id"
            class="log-item"
            :class="[`log-${log.level.toLowerCase()}`]"
          >
            <div class="log-meta">
              <span class="log-time">{{ formatTime(log.timestamp) }}</span>
              <span class="log-level" :style="{ color: levelColor(log.level) }">
                {{ log.level }}
              </span>
            </div>
            <div class="log-message" :title="log.message">
              {{ log.message }}
            </div>
            <div v-if="log.logger" class="log-source">
              {{ log.logger }}
            </div>
          </div>
        </div>
      </NScrollbar>
    </NSpin>

    <!-- 底部提示 -->
    <div class="log-footer">
      <NText depth="3" style="font-size: 11px">
        仅记录警告和错误 · 完整日志见 data/logs/app.log
      </NText>
    </div>
  </div>
</template>

<style scoped>
.log-viewer {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.log-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.log-stats {
  display: flex;
  align-items: center;
  gap: 8px;
}

.log-filter {
  display: flex;
  gap: 8px;
}

.log-empty {
  padding: 40px 0;
}

.log-list {
  display: flex;
  flex-direction: column;
}

.log-item {
  padding: 8px 10px;
  border-bottom: 1px solid var(--n-border-color);
  font-size: 12px;
  line-height: 1.5;
  transition: background-color 0.15s;
}

.log-item:hover {
  background-color: var(--n-color-hover);
}

.log-item:last-child {
  border-bottom: none;
}

.log-item.log-error,
.log-item.log-critical {
  background-color: rgba(245, 108, 108, 0.08);
}

.log-item.log-warning {
  background-color: rgba(230, 162, 60, 0.08);
}

.log-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 2px;
}

.log-time {
  color: var(--n-text-color-3);
  font-family: monospace;
  font-size: 11px;
}

.log-level {
  font-weight: 600;
  font-size: 10px;
  text-transform: uppercase;
}

.log-message {
  color: var(--n-text-color-1);
  word-break: break-word;
  white-space: pre-wrap;
}

.log-source {
  margin-top: 2px;
  color: var(--n-text-color-3);
  font-size: 10px;
  font-family: monospace;
}

.log-footer {
  text-align: center;
  padding-top: 4px;
  border-top: 1px solid var(--n-border-color);
}
</style>
