<script setup lang="ts">
/**
 * 日志设置分组（框架：① 日志记录 ② 配置弹窗）
 *
 * 阅读顺序按"查看高频 → 配置低频"排：日志记录卡（指标条 + 筛选 + 表格）独占首屏，
 * 8 个字段的配置改为卡头部「配置」按钮打开的弹窗。重构前配置是首屏第一张卡，
 * 每次进来要先滚过一张表单才看到日志；改为列表下方的卡后又要滚过整张表才能改配置。
 *
 * 逻辑：useLogConfig（配置读写）+ useLogFeed（列表/统计/筛选/自动刷新）。
 * 展示：LogRecordsCard / LogConfigModal，本组件只做组装与错误收口：
 * 配置读写失败走各自的 loadError/saveError；列表加载失败与清理/导出失败统一落
 * actionError，交给记录卡渲染成可见的错误条（不再只有 toast 与 console）。
 */
import { onMounted, ref, watch } from 'vue'
import * as logsApi from '@/shared/api/logs'
import type { SelectOption } from 'naive-ui'
import type { LogConfig, SystemLogLevel } from '@/shared/types/common'
import { useLogConfig } from '@/modules/settings/hooks/useLogConfig'
import { useLogFeed } from '@/shared/composables/useLogFeed'
import { describeError } from '@/modules/settings/hooks/useSettingsForm'
import { LOG_LEVEL_OPTIONS } from '@/modules/settings/constants'
import LogConfigModal from '@/modules/settings/components/LogConfigModal.vue'
import LogRecordsCard from '@/modules/settings/components/LogRecordsCard.vue'

// ② 日志配置
const {
  configLoading,
  configSaving,
  config,
  loadError: configLoadError,
  saveError: configSaveError,
  loadConfig,
  saveConfig,
} = useLogConfig()

// ① 列表 + 统计（筛选含模块与时间范围，故用 extraQuery 注入）
const filterLogger = ref<string | null>(null)
const filterDateRange = ref<[number, number] | null>(null)
const loggerOptions = ref<SelectOption[]>([])
const pageSize = ref(20)

const {
  loading: logsLoading,
  error: logsError,
  logs,
  total,
  page: currentPage,
  filterLevel,
  filterSearch,
  statsLoading,
  stats,
  loadLogs,
  loadStats,
  refresh,
  handleFilterChange,
  startAutoRefresh,
  stopAutoRefresh,
  errorCount,
  warningCount,
  infoCount,
} = useLogFeed({
  pageSize: () => pageSize.value,
  extraQuery: () => ({
    logger: filterLogger.value || undefined,
    start_time: filterDateRange.value ? new Date(filterDateRange.value[0]).toISOString() : undefined,
    end_time: filterDateRange.value ? new Date(filterDateRange.value[1]).toISOString() : undefined,
  }),
  autoRefreshInterval: 10000,
})

/** 自动刷新开关（useLogFeed 已按 autoRefreshInterval 在挂载时启动定时拉取） */
const autoRefresh = ref(true)
watch(autoRefresh, (enabled) => (enabled && config.value.realtime_enabled ? startAutoRefresh() : stopAutoRefresh()))
watch(() => config.value.realtime_enabled, enabled => { autoRefresh.value = enabled }, { immediate: true })

/** 清理 / 导出等操作的失败原因：显示在记录卡的操作错误条里 */
const actionError = ref('')
const toggleAutoRefresh = () => {
  if (!config.value.realtime_enabled) { actionError.value = '请先在日志配置中开启自动刷新'; return }
  autoRefresh.value = !autoRefresh.value
}
// 操作状态
const clearing = ref(false)
const exporting = ref(false)

const loadLoggers = async () => {
  try {
    const loggers = await logsApi.getLoggers()
    loggerOptions.value = [
      { label: '全部模块', value: 'all' },
      ...loggers.map((logger) => ({ label: logger, value: logger })),
    ]
  } catch (error) {
    // 模块列表拉不到只影响筛选可选项
    console.error('加载模块列表失败:', error)
  }
}

const cleanupLogs = async () => {
  clearing.value = true
  actionError.value = ''
  try {
    await logsApi.cleanupOldLogs()
    refresh()
  } catch (error) {
    actionError.value = describeError(error, '清理日志失败')
    console.error(error)
  } finally {
    clearing.value = false
  }
}

const exportLogs = async (format: 'json' | 'csv') => {
  exporting.value = true
  actionError.value = ''
  try {
    await logsApi.downloadLogs(format)
  } catch (error) {
    actionError.value = describeError(error, '导出日志失败')
    console.error(error)
  } finally {
    exporting.value = false
  }
}

const handlePageChange = (page: number) => {
  currentPage.value = page
  loadLogs()
}

const handlePageSizeChange = (size: number) => {
  pageSize.value = size
  currentPage.value = 1
  loadLogs()
}

/** 设定级别筛选并回到第一页重查（指标条点击与筛选行改级别共用） */
const handleLevelChange = (level: SystemLogLevel | null) => {
  filterLevel.value = level
  handleFilterChange()
}

/** 模块筛选与级别同为离散选择：改完立即重查 */
const handleLoggerChange = (logger: string | null) => {
  filterLogger.value = logger
  handleFilterChange()
}

/** 清除全部筛选：指标条与空态里的入口都走这里 */
const clearFilters = () => {
  filterLevel.value = null
  filterLogger.value = null
  filterSearch.value = ''
  filterDateRange.value = null
  handleFilterChange()
}

/** 配置弹窗：可见状态 + 保存（草稿交回父组件后才提交，成功才关弹窗，失败保留已填内容） */
const configModalVisible = ref(false)
const handleConfigSave = async (draft: LogConfig) => {
  Object.assign(config.value, draft)
  await saveConfig()
  if (!configSaveError.value) configModalVisible.value = false
}

onMounted(() => {
  loadLogs()
  loadStats()
  loadLoggers()
  loadConfig()
})
</script>

<template>
  <LogRecordsCard
    :logs="logs"
    :loading="logsLoading"
    :load-error="logsError"
    :action-error="actionError"
    :total="total"
    :page="currentPage"
    :page-size="pageSize"
    :stats="stats"
    :stats-loading="statsLoading"
    :error-count="errorCount"
    :warning-count="warningCount"
    :info-count="infoCount"
    :level-options="LOG_LEVEL_OPTIONS"
    :logger-options="loggerOptions"
    :filter-level="filterLevel"
    :filter-logger="filterLogger"
    :filter-search="filterSearch"
    :filter-date-range="filterDateRange"
    :retention-days="config.db_retention_days"
    :auto-refresh="autoRefresh"
    :clearing="clearing"
    :exporting="exporting"
    @update:filter-level="handleLevelChange"
    @update:filter-logger="handleLoggerChange"
    @update:filter-search="filterSearch = $event"
    @update:filter-date-range="filterDateRange = $event"
    @update:page="handlePageChange"
    @update:page-size="handlePageSizeChange"
    @filter-change="handleFilterChange"
    @clear-filters="clearFilters"
    @toggle-auto-refresh="toggleAutoRefresh"
    @retry="refresh"
    @refresh="refresh"
    @cleanup="cleanupLogs"
    @export="exportLogs"
    @open-config="configModalVisible = true"
  />

  <LogConfigModal
    v-model:show="configModalVisible"
    :config="config"
    :loading="configLoading"
    :saving="configSaving"
    :load-error="configLoadError"
    :save-error="configSaveError"
    @save="handleConfigSave"
    @retry="loadConfig"
  />
</template>
