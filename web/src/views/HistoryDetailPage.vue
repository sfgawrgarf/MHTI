<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  NCard,
  NButton,
  NTag,
  NIcon,
  NSpin,
  NEmpty,
  useMessage,
} from 'naive-ui'
import {
  ArrowBackOutline,
  StarOutline,
  RadioButtonOnOutline,
  ImageOutline,
  CalendarOutline,
  TimeOutline,
  FilmOutline,
  TvOutline,
  DocumentTextOutline,
  AlertCircleOutline,
  CheckmarkCircleOutline,
  CloseCircleOutline,
  CloudOfflineOutline,
  ConstructOutline,
} from '@vicons/ionicons5'
import { historyApi } from '@/api/history'
import { useWebSocket, type WSMessage } from '@/composables/useWebSocket'
import type { HistoryRecordDetail, TaskStatus, ScrapeLogStep } from '@/api/types'
import ResolveConflictModal from '@/components/history/ResolveConflictModal.vue'

const route = useRoute()
const router = useRouter()
const message = useMessage()
const loading = ref(false)
const record = ref<HistoryRecordDetail | null>(null)
const realtimeLogs = ref<ScrapeLogStep[]>([])

// WebSocket 相关
const {
  isConnected,
  subscribe,
  unsubscribe,
  registerHandler,
  unregisterHandler,
} = useWebSocket()

// 是否已订阅（防止重复订阅）
const subscribed = ref(false)

// 是否需要实时更新（运行中或待处理状态）
const needsRealtime = computed(() => {
  const status = record.value?.status
  return status === 'running' || status === 'pending_action'
})

const recordId = computed(() => route.params.id as string)

// ========== 处理弹窗相关 ==========

// 处理弹窗显示状态
const showHandleModal = ref(false)

// 是否可处理（pending_action 或 failed/timeout/cancelled）
const canHandle = computed(() => {
  const status = record.value?.status
  return status === 'pending_action' ||
         status === 'failed' ||
         status === 'timeout' ||
         status === 'cancelled'
})

// 处理模式：pending_action 用 resolve，其他用 retry
const handleMode = computed<'resolve' | 'retry'>(() => {
  return record.value?.status === 'pending_action' ? 'resolve' : 'retry'
})

// 处理成功回调
const handleSuccess = async () => {
  showHandleModal.value = false
  await loadRecord()
  message.success('处理完成')
}

// ========== END 处理弹窗 ==========

// 显示的日志（优先使用实时日志）
const displayLogs = computed(() => {
  if (realtimeLogs.value.length > 0) {
    return realtimeLogs.value
  }
  return record.value?.scrape_logs || []
})

// 状态标签配置
const statusConfig = computed(() => {
  const map: Record<TaskStatus, { type: 'success' | 'error' | 'warning' | 'default' | 'info'; text: string; icon: any }> = {
    success: { type: 'success', text: '成功', icon: CheckmarkCircleOutline },
    failed: { type: 'error', text: '失败', icon: CloseCircleOutline },
    timeout: { type: 'warning', text: '超时', icon: TimeOutline },
    cancelled: { type: 'warning', text: '取消', icon: AlertCircleOutline },
    skipped: { type: 'default', text: '跳过', icon: AlertCircleOutline },
    replaced: { type: 'default', text: '已替代', icon: ConstructOutline },
    pending_action: { type: 'info', text: '待处理', icon: TimeOutline },
    running: { type: 'info', text: '处理中', icon: TimeOutline },
  }
  return record.value ? map[record.value.status] : null
})

// 格式化耗时
const formatDuration = (seconds: number) => {
  if (seconds < 60) return `${seconds.toFixed(1)}秒`
  return `${(seconds / 60).toFixed(1)}分钟`
}

// ========== WebSocket 消息处理 ==========

/**
 * WebSocket 消息处理器 - 实时更新页面全部数据
 */
const handleWSMessage = (msg: WSMessage) => {
  // 只处理当前记录的消息
  if (msg.job_id !== recordId.value) return

  const { type, payload } = msg

  switch (type) {
    case 'history_detail_update':
      // 实时更新记录的所有字段
      if (record.value && payload) {
        // 状态
        if (payload.status !== undefined) {
          record.value.status = payload.status
        }
        // 日志
        if (payload.logs) {
          realtimeLogs.value = payload.logs
        }
        // 标题信息
        if (payload.title !== undefined) {
          record.value.title = payload.title
        }
        if (payload.original_title !== undefined) {
          record.value.original_title = payload.original_title
        }
        // 剧集信息
        if (payload.season_number !== undefined) {
          record.value.season_number = payload.season_number
        }
        if (payload.episode_number !== undefined) {
          record.value.episode_number = payload.episode_number
        }
        if (payload.episode_title !== undefined) {
          record.value.episode_title = payload.episode_title
        }
        if (payload.episode_overview !== undefined) {
          record.value.episode_overview = payload.episode_overview
        }
        if (payload.episode_air_date !== undefined) {
          record.value.episode_air_date = payload.episode_air_date
        }
        if (payload.episode_still_url !== undefined) {
          record.value.episode_still_url = payload.episode_still_url
        }
        // 图片
        if (payload.poster_url !== undefined) {
          record.value.poster_url = payload.poster_url
        }
        if (payload.cover_url !== undefined) {
          record.value.cover_url = payload.cover_url
        }
        if (payload.thumb_url !== undefined) {
          record.value.thumb_url = payload.thumb_url
        }
        // 元数据
        if (payload.plot !== undefined) {
          record.value.plot = payload.plot
        }
        if (payload.rating !== undefined) {
          record.value.rating = payload.rating
        }
        if (payload.votes !== undefined) {
          record.value.votes = payload.votes
        }
        if (payload.release_date !== undefined) {
          record.value.release_date = payload.release_date
        }
        if (payload.tags !== undefined) {
          record.value.tags = payload.tags
        }
        // 其他
        if (payload.duration_seconds !== undefined) {
          record.value.duration_seconds = payload.duration_seconds
        }
        if (payload.error_message !== undefined) {
          record.value.error_message = payload.error_message
        }
        if (payload.folder_path !== undefined) {
          record.value.folder_path = payload.folder_path
        }
      }
      break

    case 'history_detail_log':
      // 追加或更新日志步骤
      if (payload) {
        const existingIndex = realtimeLogs.value.findIndex(s => s.name === payload.name)
        if (existingIndex >= 0) {
          realtimeLogs.value[existingIndex] = payload
        } else {
          realtimeLogs.value.push(payload)
        }
      }
      break

    case 'history_updated':
      // 历史记录更新（兼容旧消息类型）- 实时更新所有字段
      if (payload.id === recordId.value && record.value) {
        if (payload.status !== undefined) {
          record.value.status = payload.status
        }
        if (payload.title !== undefined) {
          record.value.title = payload.title
        }
        if (payload.season_number !== undefined) {
          record.value.season_number = payload.season_number
        }
        if (payload.episode_number !== undefined) {
          record.value.episode_number = payload.episode_number
        }
      }
      break
  }
}

/**
 * 订阅当前记录的 WebSocket 更新
 */
const subscribeToRecord = () => {
  if (subscribed.value || !isConnected.value) return

  subscribe([recordId.value])
  registerHandler(handleWSMessage)
  subscribed.value = true
  console.log('[HistoryDetail] 已订阅记录:', recordId.value)
}

/**
 * 取消订阅
 */
const unsubscribeFromRecord = () => {
  if (!subscribed.value) return

  unsubscribe([recordId.value])
  unregisterHandler(handleWSMessage)
  subscribed.value = false
  console.log('[HistoryDetail] 已取消订阅记录:', recordId.value)
}

/**
 * 重连后恢复订阅
 */
const handleReconnect = async () => {
  console.log('[HistoryDetail] WebSocket 重连，恢复订阅')

  // 重置订阅状态以便重新订阅
  subscribed.value = false

  // 如果需要实时更新，重新订阅
  if (needsRealtime.value) {
    subscribeToRecord()
  }

  // 刷新数据以同步状态
  await loadRecord(false)
}

// 监听连接状态变化，实现断线重连恢复
watch(isConnected, async (connected, wasConnected) => {
  if (connected && !wasConnected && needsRealtime.value) {
    // 从断开恢复到连接
    await handleReconnect()
  }
})

// ========== END WebSocket ==========

// 加载记录详情
const loadRecord = async (showLoading = true) => {
  if (showLoading) loading.value = true
  try {
    const newRecord = await historyApi.getRecord(recordId.value)
    record.value = newRecord

    // 如果是 running 或 pending_action 状态，订阅 WebSocket 更新
    if (newRecord.status === 'running' || newRecord.status === 'pending_action') {
      subscribeToRecord()
    } else {
      // 状态已完成，取消订阅
      unsubscribeFromRecord()
    }
  } catch (error) {
    message.error('加载失败')
    console.error(error)
  } finally {
    loading.value = false
  }
}

const goBack = () => router.push('/history')

onMounted(loadRecord)

onUnmounted(() => {
  // 清理 WebSocket 订阅
  unsubscribeFromRecord()
})
</script>

<template>
  <div class="history-detail-page">
    <NSpin :show="loading">
      <!-- 返回按钮 -->
      <div class="header-nav">
        <NButton text @click="goBack" class="back-btn">
          <template #icon><NIcon :component="ArrowBackOutline" /></template>
          返回列表
        </NButton>
      </div>

      <template v-if="record">
        <!-- 主卡片 - 海报和基本信息 -->
        <NCard class="main-card glass-card">
          <div class="main-info">
            <!-- 海报 -->
            <div class="poster-wrapper">
              <div class="poster-container">
                <img v-if="record.poster_url" :src="record.poster_url" class="poster" alt="海报" />
                <div v-else class="poster-placeholder">
                  <NIcon :component="ImageOutline" size="48" />
                </div>
                <!-- 状态角标 -->
                <div v-if="statusConfig" class="status-badge" :class="statusConfig.type">
                  <NIcon :component="statusConfig.icon" :size="14" />
                  {{ statusConfig.text }}
                </div>
                <!-- 处理按钮（可处理状态时显示） -->
                <NButton
                  v-if="canHandle"
                  type="primary"
                  size="small"
                  class="handle-btn-overlay"
                  @click="showHandleModal = true"
                >
                  <template #icon><NIcon :component="ConstructOutline" /></template>
                  处理
                </NButton>
              </div>
            </div>

            <!-- 详情 -->
            <div class="info-content">
              <h1 class="title">{{ record.title || record.task_name }}</h1>

              <!-- 元信息 -->
              <div class="meta-row">
                <span v-if="record.rating" class="meta-item rating">
                  <NIcon :component="StarOutline" />
                  {{ record.rating.toFixed(1) }}
                </span>
                <span v-if="record.release_date" class="meta-item">
                  <NIcon :component="CalendarOutline" />
                  {{ record.release_date }}
                </span>
                <span v-if="record.duration_seconds" class="meta-item">
                  <NIcon :component="TimeOutline" />
                  {{ formatDuration(record.duration_seconds) }}
                </span>
              </div>

              <!-- 标签 -->
              <div v-if="record.tags.length" class="tags-row">
                <NTag v-for="tag in record.tags" :key="tag" size="small" :bordered="false" round class="meta-tag">
                  {{ tag }}
                </NTag>
              </div>

              <!-- 简介 -->
              <p v-if="record.plot" class="plot">{{ record.plot }}</p>

              <!-- 附加信息 -->
              <div v-if="record.original_title || record.translator" class="extra-info">
                <span v-if="record.original_title" class="extra-item">
                  <strong>原名:</strong> {{ record.original_title }}
                </span>
                <span v-if="record.translator" class="extra-item">
                  <strong>翻译:</strong> {{ record.translator }}
                </span>
              </div>
            </div>
          </div>
        </NCard>

        <!-- 画廊 -->
        <NCard v-if="record.thumb_url || record.episode_still_url" class="gallery-card glass-card">
          <template #header>
            <div class="card-header">
              <NIcon :component="ImageOutline" :size="20" />
              <span>画廊</span>
            </div>
          </template>
          <div class="gallery">
            <div v-if="record.thumb_url" class="gallery-item">
              <img :src="record.thumb_url" alt="缩略图" />
              <span class="gallery-label">缩略图</span>
            </div>
            <div v-if="record.episode_still_url" class="gallery-item">
              <img :src="record.episode_still_url" alt="剧照" />
              <span class="gallery-label">剧照</span>
            </div>
          </div>
        </NCard>

        <!-- 详细信息和日志 -->
        <div class="info-log-row">
          <!-- 详细信息 -->
          <NCard class="detail-card glass-card">
            <template #header>
              <div class="card-header">
                <NIcon :component="DocumentTextOutline" :size="20" />
                <span>详细信息</span>
              </div>
            </template>

            <div class="detail-sections">
              <!-- 剧集信息 -->
              <div v-if="record.season_number != null || record.episode_number != null" class="detail-section">
                <div class="section-header">
                  <NIcon :component="TvOutline" :size="16" />
                  <span>剧集信息</span>
                </div>
                <div class="section-content">
                  <div class="info-row">
                    <span v-if="record.season_number != null" class="info-badge season">
                      第 {{ record.season_number }} 季
                    </span>
                    <span v-if="record.episode_number" class="info-badge episode">
                      第 {{ record.episode_number }} 集
                    </span>
                  </div>
                  <div v-if="record.episode_title" class="info-line">
                    <span class="info-label">集标题</span>
                    <span class="info-value">{{ record.episode_title }}</span>
                  </div>
                  <div v-if="record.episode_air_date" class="info-line">
                    <span class="info-label">播出日期</span>
                    <span class="info-value">{{ record.episode_air_date }}</span>
                  </div>
                </div>
              </div>

              <!-- 标题信息 -->
              <div v-if="record.title || record.original_title" class="detail-section">
                <div class="section-header">
                  <NIcon :component="FilmOutline" :size="16" />
                  <span>标题信息</span>
                </div>
                <div class="section-content">
                  <div v-if="record.title" class="info-line">
                    <span class="info-label">剧名</span>
                    <span class="info-value">{{ record.title }}</span>
                  </div>
                  <div v-if="record.original_title" class="info-line">
                    <span class="info-label">原标题</span>
                    <span class="info-value secondary">{{ record.original_title }}</span>
                  </div>
                </div>
              </div>

              <!-- 集简介 -->
              <div v-if="record.episode_overview" class="detail-section">
                <div class="section-header">
                  <NIcon :component="DocumentTextOutline" :size="16" />
                  <span>集简介</span>
                </div>
                <div class="section-content">
                  <p class="overview-text">{{ record.episode_overview }}</p>
                </div>
              </div>

              <!-- 错误信息 -->
              <div v-if="record.error_message" class="detail-section error-section">
                <div class="section-header error">
                  <NIcon :component="AlertCircleOutline" :size="16" />
                  <span>错误信息</span>
                  <NButton
                    v-if="canHandle"
                    text
                    type="primary"
                    size="small"
                    class="section-action-btn"
                    @click="showHandleModal = true"
                  >
                    处理
                  </NButton>
                </div>
                <div class="section-content">
                  <p class="error-text">{{ record.error_message }}</p>
                </div>
              </div>

              <!-- 无信息提示 -->
              <NEmpty
                v-if="!record.season_number && !record.episode_number && !record.title && !record.episode_overview && !record.error_message"
                description="暂无详细信息"
                size="small"
              />
            </div>
          </NCard>

          <!-- 刮削日志 -->
          <NCard class="log-card glass-card">
            <template #header>
              <div class="card-header">
                <NIcon :component="FilmOutline" :size="20" />
                <span>刮削日志</span>
                <!-- WebSocket 连接状态 -->
                <NTag v-if="isConnected && needsRealtime" type="success" size="small" round class="live-tag">
                  <template #icon><NIcon :component="RadioButtonOnOutline" /></template>
                  实时
                </NTag>
                <NTag v-else-if="!isConnected && needsRealtime" type="warning" size="small" round class="live-tag">
                  <template #icon><NIcon :component="CloudOfflineOutline" /></template>
                  连接断开
                </NTag>
              </div>
            </template>

            <NEmpty v-if="!displayLogs.length" description="暂无日志" />
            <div v-else class="log-list">
              <div
                v-for="(step, index) in displayLogs"
                :key="index"
                class="log-step-item"
              >
                <div class="step-line">
                  <span class="step-dot" :class="step.completed ? 'success' : 'error'"></span>
                  <span class="step-title">{{ step.name }}</span>
                  <NTag :type="step.completed ? 'success' : 'error'" size="tiny" round>
                    {{ step.completed ? '完成' : '失败' }}
                  </NTag>
                </div>
                <div v-if="step.logs.length" class="step-messages">
                  <div
                    v-for="(log, logIndex) in step.logs"
                    :key="logIndex"
                    class="msg-item"
                    :class="log.level"
                  >
                    {{ log.message }}
                  </div>
                </div>
              </div>
            </div>
          </NCard>
        </div>
      </template>
    </NSpin>

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
  max-width: 1200px;
  margin: 0 auto;
}

.header-nav {
  margin-bottom: 16px;
}

.back-btn {
  font-weight: 500;
}

.back-btn:hover {
  color: var(--n-primary-color);
}

/* 毛玻璃卡片 */
.glass-card {
  border: none;
  background: var(--ios-glass-bg-thick);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  margin-bottom: 16px;
}

/* 主卡片 */
.main-card {
  overflow: hidden;
}

.main-info {
  display: flex;
  gap: 24px;
}

.poster-wrapper {
  flex-shrink: 0;
  width: 200px;
}

.poster-container {
  position: relative;
  border-radius: 12px;
  overflow: hidden;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.15);
}

.poster {
  width: 100%;
  aspect-ratio: 2/3;
  object-fit: cover;
  display: block;
}

.poster-placeholder {
  width: 100%;
  aspect-ratio: 2/3;
  background: linear-gradient(135deg, var(--n-color-embedded) 0%, var(--n-border-color) 100%);
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--n-text-color-3);
}

.status-badge {
  position: absolute;
  top: 12px;
  right: 12px;
  padding: 4px 10px;
  border-radius: 20px;
  font-size: 12px;
  font-weight: 600;
  display: flex;
  align-items: center;
  gap: 4px;
  backdrop-filter: blur(8px);
}

.status-badge.success {
  background: rgba(16, 185, 129, 0.9);
  color: white;
}

.status-badge.error {
  background: rgba(239, 68, 68, 0.9);
  color: white;
}

.status-badge.warning {
  background: rgba(245, 158, 11, 0.9);
  color: white;
}

.status-badge.info {
  background: rgba(59, 130, 246, 0.9);
  color: white;
}

.status-badge.default {
  background: rgba(107, 114, 128, 0.9);
  color: white;
}

.info-content {
  flex: 1;
  min-width: 0;
}

.title {
  font-size: 24px;
  font-weight: 700;
  margin: 0 0 12px 0;
  line-height: 1.3;
  color: var(--n-text-color-1);
}

.meta-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 16px;
  margin-bottom: 16px;
}

.meta-item {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 14px;
  color: var(--n-text-color-2);
}

.meta-item.rating {
  color: #f59e0b;
  font-weight: 600;
}

.tags-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 16px;
}

.meta-tag {
  background: linear-gradient(135deg, rgba(99, 102, 241, 0.1) 0%, rgba(139, 92, 246, 0.1) 100%);
  color: var(--n-text-color-2);
}

.plot {
  color: var(--n-text-color-2);
  font-size: 14px;
  line-height: 1.8;
  margin: 0 0 16px 0;
  display: -webkit-box;
  -webkit-line-clamp: 4;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.extra-info {
  display: flex;
  flex-wrap: wrap;
  gap: 20px;
}

.extra-item {
  font-size: 13px;
  color: var(--n-text-color-3);
}

.extra-item strong {
  color: var(--n-text-color-2);
}

/* 信息和日志行 */
.info-log-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
}

.card-header {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 600;
}

.live-tag {
  margin-left: auto;
}

/* 详细信息分组样式 */
.detail-sections {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.detail-section {
  background: var(--n-color-embedded);
  border-radius: 12px;
  overflow: hidden;
}

.detail-section.error-section {
  background: rgba(239, 68, 68, 0.08);
}

.section-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 16px;
  background: rgba(99, 102, 241, 0.08);
  font-size: 13px;
  font-weight: 600;
  color: var(--n-primary-color);
}

.section-header.error {
  background: rgba(239, 68, 68, 0.12);
  color: #ef4444;
}

.section-content {
  padding: 16px;
}

.info-row {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-bottom: 12px;
}

.info-badge {
  display: inline-flex;
  align-items: center;
  padding: 6px 14px;
  border-radius: 20px;
  font-size: 14px;
  font-weight: 600;
}

.info-badge.season {
  background: linear-gradient(135deg, rgba(99, 102, 241, 0.15) 0%, rgba(139, 92, 246, 0.15) 100%);
  color: var(--n-primary-color);
}

.info-badge.episode {
  background: linear-gradient(135deg, rgba(16, 185, 129, 0.15) 0%, rgba(52, 211, 153, 0.15) 100%);
  color: #10b981;
}

.info-line {
  display: flex;
  align-items: baseline;
  gap: 12px;
  padding: 8px 0;
  border-bottom: 1px dashed var(--n-border-color);
}

.info-line:last-child {
  border-bottom: none;
  padding-bottom: 0;
}

.info-label {
  flex-shrink: 0;
  width: 70px;
  font-size: 13px;
  color: var(--n-text-color-3);
}

.info-value {
  flex: 1;
  font-size: 14px;
  color: var(--n-text-color-1);
  word-break: break-all;
}

.info-value.secondary {
  color: var(--n-text-color-2);
}

.overview-text {
  margin: 0;
  font-size: 14px;
  line-height: 1.8;
  color: var(--n-text-color-2);
}

.error-text {
  margin: 0;
  font-size: 14px;
  line-height: 1.6;
  color: #ef4444;
}

/* 刮削日志样式 */
.log-list {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.log-step-item {
  position: relative;
}

.log-step-item:not(:last-child)::after {
  content: '';
  position: absolute;
  left: 5px;
  top: 24px;
  bottom: -12px;
  width: 2px;
  background: var(--n-border-color);
}

.step-line {
  display: flex;
  align-items: center;
  gap: 12px;
}

.step-dot {
  width: 12px;
  height: 12px;
  border-radius: 50%;
  flex-shrink: 0;
  position: relative;
  z-index: 1;
}

.step-dot.success {
  background: #10b981;
  box-shadow: 0 0 0 4px rgba(16, 185, 129, 0.15);
}

.step-dot.error {
  background: #ef4444;
  box-shadow: 0 0 0 4px rgba(239, 68, 68, 0.15);
}

.step-title {
  flex: 1;
  font-size: 14px;
  font-weight: 500;
  color: var(--n-text-color-1);
}

.step-messages {
  margin-left: 24px;
  margin-top: 8px;
  padding-left: 12px;
  border-left: 2px solid var(--n-border-color);
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.msg-item {
  font-size: 13px;
  color: var(--n-text-color-3);
  line-height: 1.5;
  padding: 4px 0;
}

.msg-item.success {
  color: #10b981;
}

.msg-item.warning {
  color: #f59e0b;
}

.msg-item.error {
  color: #ef4444;
}

/* 画廊 */
.gallery {
  display: flex;
  gap: 16px;
  overflow-x: auto;
  padding: 8px 0;
}

.gallery-item {
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
  align-items: center;
}

.gallery-item img {
  height: 160px;
  border-radius: 8px;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
  transition: transform 0.2s ease;
}

.gallery-item img:hover {
  transform: scale(1.02);
}

.gallery-label {
  font-size: 12px;
  color: var(--n-text-color-3);
}

/* 处理按钮 */
.handle-btn-overlay {
  position: absolute;
  bottom: 12px;
  right: 12px;
  box-shadow: 0 4px 12px rgba(99, 102, 241, 0.3);
}

.section-action-btn {
  margin-left: auto;
}

/* 响应式 */
@media (max-width: 768px) {
  .main-info {
    flex-direction: column;
    align-items: center;
    text-align: center;
  }

  .poster-wrapper {
    width: 160px;
  }

  .meta-row, .tags-row, .extra-info {
    justify-content: center;
  }

  .info-log-row {
    grid-template-columns: 1fr;
  }

  .detail-grid {
    grid-template-columns: 1fr;
  }

  .detail-item.full-width {
    grid-column: 1;
  }
}
</style>
