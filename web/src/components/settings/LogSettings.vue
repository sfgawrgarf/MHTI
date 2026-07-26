<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, h } from 'vue'
import {
  NCard,
  NSpace,
  NButton,
  NInputNumber,
  NFormItem,
  NSelect,
  NSwitch,
  NDataTable,
  NTag,
  NInput,
  NPagination,
  NDatePicker,
  NStatistic,
  NGrid,
  NGi,
  NPopconfirm,
  NSpin,
  NText,
  useMessage,
  type DataTableColumns,
  type SelectOption,
} from 'naive-ui'
import * as logsApi from '@/api/logs'
import type { LogConfig, LogStats, SystemLogEntry, SystemLogLevel } from '@/api/types'
import { formatDateTime } from '@/utils/format'

const message = useMessage()

// 配置状态
const configLoading = ref(false)
const configSaving = ref(false)
const config = ref<LogConfig>({
  log_level: 'INFO',
  console_enabled: true,
  file_enabled: true,
  db_enabled: true,
  max_file_size_mb: 10,
  max_file_count: 5,
  db_retention_days: 30,
  realtime_enabled: true,
})

// 日志列表状态
const logsLoading = ref(false)
const logs = ref<SystemLogEntry[]>([])
const totalLogs = ref(0)
const currentPage = ref(1)
const pageSize = ref(20)

// 筛选状态
const filterLevel = ref<SystemLogLevel | null>(null)
const filterLogger = ref<string | null>(null)
const filterSearch = ref('')
const filterDateRange = ref<[number, number] | null>(null)

// 统计状态
const statsLoading = ref(false)
const stats = ref<LogStats | null>(null)

// 模块列表
const loggerOptions = ref<SelectOption[]>([])

// 操作状态
const clearing = ref(false)
const exporting = ref(false)

// 日志级别选项
const levelOptions = [
  { label: '全部', value: null },
  { label: 'DEBUG', value: 'DEBUG' },
  { label: 'INFO', value: 'INFO' },
  { label: 'WARNING', value: 'WARNING' },
  { label: 'ERROR', value: 'ERROR' },
  { label: 'CRITICAL', value: 'CRITICAL' },
] as unknown as SelectOption[]

// 日志级别标签样式
const levelTagType = (level: SystemLogLevel) => {
  const typeMap: Record<SystemLogLevel, 'default' | 'info' | 'warning' | 'error'> = {
    DEBUG: 'default',
    INFO: 'info',
    WARNING: 'warning',
    ERROR: 'error',
    CRITICAL: 'error',
  }
  return typeMap[level]
}

// 表格列定义
const columns: DataTableColumns<SystemLogEntry> = [
  {
    title: '时间',
    key: 'timestamp',
    width: 180,
    render: (row) => formatDateTime(row.timestamp),
  },
  {
    title: '级别',
    key: 'level',
    width: 100,
    render: (row) =>
      h(NTag, { type: levelTagType(row.level), size: 'small' }, () => row.level),
  },
  {
    title: '模块',
    key: 'logger',
    width: 200,
    ellipsis: { tooltip: true },
  },
  {
    title: '消息',
    key: 'message',
    ellipsis: { tooltip: true },
  },
]

// 加载配置
const loadConfig = async () => {
  configLoading.value = true
  try {
    config.value = await logsApi.getLogConfig()
  } catch (error) {
    console.error('加载日志配置失败:', error)
  } finally {
    configLoading.value = false
  }
}

// 保存配置
const saveConfig = async () => {
  configSaving.value = true
  try {
    config.value = await logsApi.updateLogConfig(config.value)
    message.success('日志配置已保存')
  } catch (error) {
    message.error('保存配置失败')
    console.error(error)
  } finally {
    configSaving.value = false
  }
}

// 加载日志列表
const loadLogs = async () => {
  logsLoading.value = true
  try {
    const response = await logsApi.getLogs({
      level: filterLevel.value || undefined,
      logger: filterLogger.value || undefined,
      search: filterSearch.value || undefined,
      start_time: filterDateRange.value ? new Date(filterDateRange.value[0]).toISOString() : undefined,
      end_time: filterDateRange.value ? new Date(filterDateRange.value[1]).toISOString() : undefined,
      page: currentPage.value,
      page_size: pageSize.value,
    })
    logs.value = response.items
    totalLogs.value = response.total
  } catch (error) {
    console.error('加载日志失败:', error)
  } finally {
    logsLoading.value = false
  }
}

// 加载统计信息
const loadStats = async () => {
  statsLoading.value = true
  try {
    stats.value = await logsApi.getLogStats()
  } catch (error) {
    console.error('加载统计失败:', error)
  } finally {
    statsLoading.value = false
  }
}

// 加载模块列表
const loadLoggers = async () => {
  try {
    const loggers = await logsApi.getLoggers()
    loggerOptions.value = [
      { label: '全部模块', value: null },
      ...loggers.map((l) => ({ label: l, value: l })),
    ] as unknown as SelectOption[]
  } catch (error) {
    console.error('加载模块列表失败:', error)
  }
}

// 刷新日志
const refreshLogs = () => {
  currentPage.value = 1
  loadLogs()
  loadStats()
}

// 清理过期日志
const cleanupLogs = async () => {
  clearing.value = true
  try {
    const result = await logsApi.cleanupOldLogs()
    message.success(result.message)
    refreshLogs()
  } catch (error) {
    message.error('清理日志失败')
    console.error(error)
  } finally {
    clearing.value = false
  }
}

// 导出日志
const exportLogs = async (format: 'json' | 'csv') => {
  exporting.value = true
  try {
    await logsApi.downloadLogs(format)
    message.success(`日志已导出为 ${format.toUpperCase()} 格式`)
  } catch (error) {
    message.error('导出日志失败')
    console.error(error)
  } finally {
    exporting.value = false
  }
}

// 页码变化
const handlePageChange = (page: number) => {
  currentPage.value = page
  loadLogs()
}

// 页大小变化
const handlePageSizeChange = (size: number) => {
  pageSize.value = size
  currentPage.value = 1
  loadLogs()
}

// 筛选变化时刷新
const handleFilterChange = () => {
  currentPage.value = 1
  loadLogs()
}

// 自动刷新
let refreshInterval: number | null = null

const startAutoRefresh = () => {
  if (refreshInterval) return
  refreshInterval = window.setInterval(() => {
    if (!logsLoading.value) {
      loadLogs()
    }
  }, 10000) // 每 10 秒刷新
}

const stopAutoRefresh = () => {
  if (refreshInterval) {
    clearInterval(refreshInterval)
    refreshInterval = null
  }
}

onMounted(() => {
  loadConfig()
  loadLogs()
  loadStats()
  loadLoggers()
  startAutoRefresh()
})

onUnmounted(() => {
  stopAutoRefresh()
})

// 计算统计标签
const errorCount = computed(() => stats.value?.by_level?.ERROR || 0)
const warningCount = computed(() => stats.value?.by_level?.WARNING || 0)
const infoCount = computed(() => stats.value?.by_level?.INFO || 0)
</script>

<template>
  <NSpace vertical size="large">
    <!-- 日志配置 -->
    <NCard title="日志配置" size="small">
      <NSpin :show="configLoading">
        <NSpace vertical size="large">
          <NGrid :cols="2" :x-gap="24" :y-gap="16">
            <NGi>
              <NFormItem label="日志级别">
                <NSelect
                  v-model:value="config.log_level"
                  :options="levelOptions.filter(o => o.value)"
                  style="width: 150px"
                />
                <NText depth="3" style="margin-left: 8px">低于此级别的日志将不被记录</NText>
              </NFormItem>
            </NGi>
            <NGi>
              <NFormItem label="保留天数">
                <NInputNumber
                  v-model:value="config.db_retention_days"
                  :min="1"
                  :max="365"
                  style="width: 120px"
                />
                <NText depth="3" style="margin-left: 8px">超过此天数的日志将被自动清理</NText>
              </NFormItem>
            </NGi>
            <NGi>
              <NFormItem label="控制台输出">
                <NSwitch v-model:value="config.console_enabled" />
              </NFormItem>
            </NGi>
            <NGi>
              <NFormItem label="文件输出">
                <NSwitch v-model:value="config.file_enabled" />
              </NFormItem>
            </NGi>
            <NGi>
              <NFormItem label="数据库存储">
                <NSwitch v-model:value="config.db_enabled" />
              </NFormItem>
            </NGi>
            <NGi>
              <NFormItem label="实时推送">
                <NSwitch v-model:value="config.realtime_enabled" />
              </NFormItem>
            </NGi>
            <NGi>
              <NFormItem label="单文件大小限制 (MB)">
                <NInputNumber
                  v-model:value="config.max_file_size_mb"
                  :min="1"
                  :max="100"
                  style="width: 120px"
                />
              </NFormItem>
            </NGi>
            <NGi>
              <NFormItem label="日志文件数量">
                <NInputNumber
                  v-model:value="config.max_file_count"
                  :min="1"
                  :max="20"
                  style="width: 120px"
                />
              </NFormItem>
            </NGi>
          </NGrid>

          <NButton type="primary" :loading="configSaving" @click="saveConfig">
            保存配置
          </NButton>
        </NSpace>
      </NSpin>
    </NCard>

    <!-- 日志统计 -->
    <NCard title="日志统计" size="small">
      <NSpin :show="statsLoading">
        <NGrid :cols="4" :x-gap="24">
          <NGi>
            <NStatistic label="总日志数" :value="stats?.total || 0" />
          </NGi>
          <NGi>
            <NStatistic label="错误日志" :value="errorCount">
              <template #suffix>
                <NTag v-if="errorCount > 0" type="error" size="small">需关注</NTag>
              </template>
            </NStatistic>
          </NGi>
          <NGi>
            <NStatistic label="警告日志" :value="warningCount" />
          </NGi>
          <NGi>
            <NStatistic label="信息日志" :value="infoCount" />
          </NGi>
        </NGrid>
      </NSpin>
    </NCard>

    <!-- 日志列表 -->
    <NCard title="日志列表" size="small">
      <template #header-extra>
        <NSpace>
          <NButton size="small" @click="refreshLogs">刷新</NButton>
          <NPopconfirm @positive-click="cleanupLogs">
            <template #trigger>
              <NButton size="small" :loading="clearing">清理过期</NButton>
            </template>
            确定清理超过 {{ config.db_retention_days }} 天的日志？
          </NPopconfirm>
          <NButton size="small" :loading="exporting" @click="exportLogs('json')">
            导出 JSON
          </NButton>
          <NButton size="small" :loading="exporting" @click="exportLogs('csv')">
            导出 CSV
          </NButton>
        </NSpace>
      </template>

      <!-- 筛选条件 -->
      <NSpace style="margin-bottom: 16px">
        <NSelect
          v-model:value="filterLevel"
          :options="levelOptions"
          placeholder="日志级别"
          style="width: 120px"
          clearable
          @update:value="handleFilterChange"
        />
        <NSelect
          v-model:value="filterLogger"
          :options="loggerOptions"
          placeholder="选择模块"
          style="width: 200px"
          clearable
          filterable
          @update:value="handleFilterChange"
        />
        <NInput
          v-model:value="filterSearch"
          placeholder="搜索日志内容..."
          style="width: 200px"
          clearable
          @keyup.enter="handleFilterChange"
          @clear="handleFilterChange"
        />
        <NDatePicker
          v-model:value="filterDateRange"
          type="datetimerange"
          clearable
          @update:value="handleFilterChange"
        />
        <NButton @click="handleFilterChange">搜索</NButton>
      </NSpace>

      <!-- 日志表格 -->
      <NDataTable
        :columns="columns"
        :data="logs"
        :loading="logsLoading"
        :row-key="(row: SystemLogEntry) => row.id"
        size="small"
        striped
        max-height="400px"
      />

      <!-- 分页 -->
      <NSpace justify="end" style="margin-top: 16px">
        <NPagination
          v-model:page="currentPage"
          v-model:page-size="pageSize"
          :item-count="totalLogs"
          :page-sizes="[10, 20, 50, 100]"
          show-size-picker
          @update:page="handlePageChange"
          @update:page-size="handlePageSizeChange"
        />
      </NSpace>
    </NCard>
  </NSpace>
</template>
