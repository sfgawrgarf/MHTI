<script setup lang="ts">
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import {
  NCard,
  NGrid,
  NGi,
  NIcon,
  NTag,
} from 'naive-ui'
import {
  CheckmarkCircleOutline,
  CloseCircleOutline,
  FolderOutline,
  SearchOutline,
  TimeOutline,
  TrendingUpOutline,
  FilmOutline,
} from '@vicons/ionicons5'
import { useHomeStats } from '@/composables/useHomeStats'
import { useResponsiveValue } from '@/composables/useMobileLayout'
import type { HistoryRecord } from '@/api/types'
import HomeSkeleton from '@/components/common/HomeSkeleton.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import AnimatedNumber from '@/components/common/AnimatedNumber.vue'
import RingChart from '@/components/common/RingChart.vue'
import BarChart from '@/components/common/BarChart.vue'
import TmdbSetupBanner from '@/components/common/TmdbSetupBanner.vue'

const router = useRouter()
const { loading, totalTasks, successCount, failedCount, recentTasks, weeklyStats } = useHomeStats()

// 响应式图表尺寸
const ringChartSize = useResponsiveValue({ mobile: 80, tablet: 90, desktop: 100 })
const barChartHeight = useResponsiveValue({ mobile: 80, tablet: 90, desktop: 100 })

// iOS 风格统计卡片配置
const statCards = [
  { key: 'total', label: '总任务数', icon: TrendingUpOutline, color: '#007AFF', bgColor: 'rgba(0, 122, 255, 0.1)', path: '/history' },
  { key: 'success', label: '成功', icon: CheckmarkCircleOutline, color: '#34C759', bgColor: 'rgba(52, 199, 89, 0.1)', path: '/history?status=success' },
  { key: 'failed', label: '失败', icon: CloseCircleOutline, color: '#FF3B30', bgColor: 'rgba(255, 59, 48, 0.1)', path: '/history?status=failed' },
]

// iOS 风格快捷入口配置
const quickLinks = [
  { title: '手动任务', desc: '创建新的刮削任务', icon: SearchOutline, path: '/scan', color: '#007AFF', bgColor: 'rgba(0, 122, 255, 0.1)' },
  { title: '刮削记录', desc: '查看历史记录', icon: TimeOutline, path: '/history', color: '#5856D6', bgColor: 'rgba(88, 86, 214, 0.1)' },
  { title: '文件管理', desc: '浏览媒体文件', icon: FolderOutline, path: '/files', color: '#FF9500', bgColor: 'rgba(255, 149, 0, 0.1)' },
]

// 环形图数据 - iOS 色彩
const ringChartData = computed(() => [
  { label: '成功', value: successCount.value, color: '#34C759' },
  { label: '失败', value: failedCount.value, color: '#FF3B30' },
])

const goTo = (path: string) => router.push(path)

const getStatValue = (key: string) => {
  switch (key) {
    case 'total': return totalTasks.value
    case 'success': return successCount.value
    case 'failed': return failedCount.value
    default: return 0
  }
}

const handleCreateTask = () => router.push('/scan')

const getStatusConfig = (status: string) => {
  const map: Record<string, { type: 'success' | 'error' | 'warning'; text: string; color: string }> = {
    success: { type: 'success', text: '成功', color: '#34C759' },
    failed: { type: 'error', text: '失败', color: '#FF3B30' },
    cancelled: { type: 'warning', text: '取消', color: '#FF9500' },
  }
  return map[status] || { type: 'warning', text: status, color: '#FF9500' }
}

const formatTime = (time: string) => {
  if (!time) return '-'
  const date = new Date(time)
  const now = new Date()
  const diff = now.getTime() - date.getTime()
  const minutes = Math.floor(diff / 60000)
  const hours = Math.floor(diff / 3600000)
  const days = Math.floor(diff / 86400000)

  if (minutes < 1) return '刚刚'
  if (minutes < 60) return `${minutes}分钟前`
  if (hours < 24) return `${hours}小时前`
  if (days < 7) return `${days}天前`
  return time.replace('T', ' ').slice(0, 10)
}

const goToDetail = (record: HistoryRecord) => {
  router.push(`/history/${record.id}`)
}
</script>

<template>
  <div class="home-page">
    <!-- 骨架屏 -->
    <HomeSkeleton v-if="loading" />

    <!-- 实际内容 -->
    <template v-else>
      <!-- TMDB 配置提示 -->
      <TmdbSetupBanner />

      <!-- 统计卡片 -->
      <NGrid :x-gap="16" :y-gap="16" cols="1 s:2 m:3" responsive="screen">
        <NGi v-for="(stat, index) in statCards" :key="stat.key">
          <NCard
            class="stat-card ios-card"
            :style="{ '--delay': `${index * 0.08}s` }"
            hoverable
            @click="goTo(stat.path)"
          >
            <div class="stat-content">
              <div class="stat-icon" :style="{ background: stat.bgColor }">
                <NIcon :component="stat.icon" :size="26" :color="stat.color" />
              </div>
              <div class="stat-info">
                <div class="stat-value">
                  <AnimatedNumber :value="getStatValue(stat.key)" />
                </div>
                <span class="stat-label">{{ stat.label }}</span>
              </div>
            </div>
          </NCard>
        </NGi>
      </NGrid>

      <!-- 数据可视化 -->
      <NGrid :x-gap="16" :y-gap="16" cols="1 m:2" responsive="screen" style="margin-top: 16px;">
        <NGi>
          <NCard class="chart-card ios-card" title="任务统计" :style="{ '--delay': '0.24s' }">
            <RingChart :data="ringChartData" :size="ringChartSize" />
          </NCard>
        </NGi>
        <NGi>
          <NCard class="chart-card ios-card" title="近7天趋势" :style="{ '--delay': '0.32s' }">
            <BarChart :data="weeklyStats" :height="barChartHeight" />
          </NCard>
        </NGi>
      </NGrid>

      <!-- 快捷入口 -->
      <div class="section-title">快捷入口</div>
      <NGrid :x-gap="16" :y-gap="16" cols="1 s:2 m:3" responsive="screen">
        <NGi v-for="(link, index) in quickLinks" :key="link.path">
          <NCard
            class="quick-link ios-card"
            hoverable
            :style="{ '--delay': `${0.4 + index * 0.08}s` }"
            @click="goTo(link.path)"
          >
            <div class="link-content">
              <div class="link-icon" :style="{ background: link.bgColor }">
                <NIcon :component="link.icon" :size="28" :color="link.color" />
              </div>
              <div class="link-info">
                <span class="link-title">{{ link.title }}</span>
                <span class="link-desc">{{ link.desc }}</span>
              </div>
            </div>
          </NCard>
        </NGi>
      </NGrid>

      <!-- 最近任务 -->
      <NCard class="recent-card ios-card" :style="{ '--delay': '0.64s' }">
        <template #header>
          <div class="card-header">
            <span class="card-title">最近任务</span>
            <a class="view-all" @click="goTo('/history')">查看全部 →</a>
          </div>
        </template>
        <EmptyState
          v-if="recentTasks.length === 0"
          title="暂无任务记录"
          description="开始你的第一个刮削任务吧"
          action-text="创建任务"
          @action="handleCreateTask"
        />
        <div v-else class="task-list">
          <div
            v-for="task in recentTasks"
            :key="task.id"
            class="task-item"
            @click="goToDetail(task)"
          >
            <div class="task-icon">
              <NIcon :component="FilmOutline" :size="20" />
            </div>
            <div class="task-info">
              <div class="task-name">{{ task.title || task.task_name }}</div>
              <div class="task-meta">
                <span v-if="task.season_number || task.episode_number" class="task-episode">
                  S{{ String(task.season_number || 0).padStart(2, '0') }}E{{ String(task.episode_number || 0).padStart(2, '0') }}
                </span>
                <span class="task-time">{{ formatTime(task.executed_at) }}</span>
              </div>
            </div>
            <div class="task-status">
              <NTag :type="getStatusConfig(task.status).type" size="small" round>
                {{ getStatusConfig(task.status).text }}
              </NTag>
            </div>
          </div>
        </div>
      </NCard>
    </template>
  </div>
</template>

<style scoped>
.home-page {
  max-width: 1200px;
  margin: 0 auto;
}

/* iOS 风格卡片 */
.ios-card {
  border: none;
  background: var(--ios-bg-secondary);
  border-radius: 16px;
  box-shadow: var(--ios-shadow-sm);
  animation: ios-card-enter 0.5s ease forwards;
  animation-delay: var(--delay, 0s);
  opacity: 0;
  transform: translateY(16px);
}

@keyframes ios-card-enter {
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

/* 统计卡片 */
.stat-card {
  transition: transform 0.25s ease, box-shadow 0.25s ease;
  cursor: pointer;
}

.stat-card:hover {
  transform: translateY(-4px);
  box-shadow: var(--ios-shadow-md);
}

.stat-card:active {
  transform: translateY(-2px) scale(0.99);
}

.stat-content {
  display: flex;
  align-items: center;
  gap: 16px;
}

.stat-icon {
  width: 56px;
  height: 56px;
  border-radius: 14px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.stat-info {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.stat-value {
  font-size: 32px;
  font-weight: 700;
  color: var(--ios-text-primary);
  letter-spacing: -1px;
}

.stat-label {
  font-size: 14px;
  color: var(--ios-text-secondary);
  font-weight: 500;
}

/* 图表卡片 */
.chart-card {
  min-height: 180px;
}

.chart-card :deep(.n-card__content) {
  display: flex;
  align-items: center;
  justify-content: center;
}

.chart-card :deep(.n-card-header__main) {
  font-weight: 600;
  color: var(--ios-text-primary);
}

/* 区块标题 */
.section-title {
  font-size: 15px;
  font-weight: 600;
  margin: 28px 0 16px;
  color: var(--ios-text-secondary);
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

/* 快捷入口 */
.quick-link {
  cursor: pointer;
  transition: transform 0.25s ease, box-shadow 0.25s ease;
}

.quick-link:hover {
  transform: translateY(-4px) scale(1.01);
  box-shadow: var(--ios-shadow-lg);
}

.link-content {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 4px 0;
}

.link-icon {
  width: 56px;
  height: 56px;
  border-radius: 14px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.link-info {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.link-title {
  font-size: 17px;
  font-weight: 600;
  color: var(--ios-text-primary);
}

.link-desc {
  font-size: 14px;
  color: var(--ios-text-secondary);
}

/* 最近任务卡片 */
.recent-card {
  margin-top: 24px;
}

.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
}

.card-title {
  font-size: 17px;
  font-weight: 600;
  color: var(--ios-text-primary);
}

.view-all {
  color: var(--ios-blue);
  cursor: pointer;
  font-size: 15px;
  font-weight: 500;
  transition: opacity 0.2s ease;
}

.view-all:hover {
  opacity: 0.7;
}

/* iOS 风格任务列表 */
.task-list {
  display: flex;
  flex-direction: column;
}

.task-item {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 14px 0;
  border-bottom: 1px solid var(--ios-separator);
  cursor: pointer;
  transition: background 0.15s ease;
  margin: 0 -20px;
  padding-left: 20px;
  padding-right: 20px;
}

.task-item:last-child {
  border-bottom: none;
}

.task-item:hover {
  background: var(--ios-bg-tertiary);
}

.task-item:active {
  background: var(--ios-separator);
}

.task-icon {
  width: 44px;
  height: 44px;
  border-radius: 12px;
  background: var(--ios-blue-light);
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--ios-blue);
  flex-shrink: 0;
}

.task-info {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.task-name {
  font-size: 16px;
  font-weight: 500;
  color: var(--ios-text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.task-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: var(--ios-text-secondary);
}

.task-episode {
  font-family: 'SF Mono', Monaco, monospace;
  color: var(--ios-blue);
  font-weight: 500;
}

.task-time {
  color: var(--ios-text-tertiary);
}

.task-status {
  flex-shrink: 0;
}

/* 移动端优化 */
@media (max-width: 768px) {
  .home-page {
    padding: 0;
  }

  .stat-content {
    gap: 12px;
  }

  .stat-icon {
    width: 48px;
    height: 48px;
    border-radius: 12px;
  }

  .stat-value {
    font-size: 26px;
  }

  .stat-label {
    font-size: 13px;
  }

  .link-icon {
    width: 48px;
    height: 48px;
    border-radius: 12px;
  }

  .link-title {
    font-size: 15px;
  }

  .link-desc {
    font-size: 13px;
  }

  .chart-card {
    min-height: 140px;
  }

  .section-title {
    margin: 20px 0 12px;
    font-size: 13px;
  }

  .task-item {
    padding: 12px 16px;
    margin: 0 -16px;
    gap: 12px;
  }

  .task-icon {
    width: 40px;
    height: 40px;
    border-radius: 10px;
  }

  .task-name {
    font-size: 15px;
  }

  .task-meta {
    font-size: 12px;
  }

  .card-header {
    flex-wrap: wrap;
    gap: 8px;
  }

  .card-title {
    font-size: 15px;
  }

  .view-all {
    font-size: 14px;
  }

  .recent-card {
    margin-top: 16px;
  }

  /* 减少入场动画延迟 */
  .ios-card {
    animation-duration: 0.35s;
  }
}

/* 触摸设备优化 */
@media (hover: none) and (pointer: coarse) {
  .stat-card:hover,
  .quick-link:hover {
    transform: none;
    box-shadow: var(--shadow-sm, 0 1px 3px rgba(0, 0, 0, 0.06));
  }

  .stat-card:active,
  .quick-link:active {
    transform: scale(0.98);
    opacity: 0.9;
  }

  .task-item:hover {
    background: transparent;
  }

  .task-item:active {
    background: var(--color-fill-secondary, rgba(0, 0, 0, 0.04));
  }
}

/* 减少动画 */
@media (prefers-reduced-motion: reduce) {
  .ios-card {
    animation: none;
    opacity: 1;
    transform: none;
  }

  .stat-card,
  .quick-link {
    transition: none;
  }
}
</style>
