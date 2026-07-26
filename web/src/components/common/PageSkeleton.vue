<script setup lang="ts">
import { computed } from 'vue'
import { NCard, NGrid, NGi } from 'naive-ui'
import Skeleton from './Skeleton.vue'
import { useMobileLayout } from '@/composables/useMobileLayout'

/**
 * 页面骨架屏组件
 * 支持多种页面布局预设，自动适配移动端
 */

export type SkeletonPreset = 'list' | 'grid' | 'detail' | 'form' | 'table' | 'cards'

const props = withDefaults(defineProps<{
  /** 预设布局类型 */
  preset?: SkeletonPreset
  /** 列表/网格项数量 */
  count?: number
  /** 是否显示标题区域 */
  showHeader?: boolean
  /** 网格列数（仅 grid/cards 预设） */
  cols?: number
}>(), {
  preset: 'list',
  count: 5,
  showHeader: true,
  cols: 3,
})

const { isMobile } = useMobileLayout()

// 响应式列数字符串（Naive UI 格式）
const responsiveCols = computed(() => `1 s:2 m:${props.cols}`)
</script>

<template>
  <div class="page-skeleton">
    <!-- 页面标题骨架 -->
    <div v-if="showHeader" class="page-header-skeleton">
      <Skeleton width="200px" height="28px" />
      <Skeleton width="300px" height="16px" />
    </div>

    <!-- 列表预设 -->
    <template v-if="preset === 'list'">
      <div class="list-skeleton">
        <div v-for="i in count" :key="i" class="list-item-skeleton">
          <Skeleton type="circle" width="48px" height="48px" />
          <div class="list-item-content">
            <Skeleton width="60%" height="18px" />
            <Skeleton width="40%" height="14px" />
          </div>
          <Skeleton width="80px" height="32px" />
        </div>
      </div>
    </template>

    <!-- 网格预设 -->
    <template v-else-if="preset === 'grid'">
      <NGrid :x-gap="16" :y-gap="16" :cols="responsiveCols" responsive="screen">
        <NGi v-for="i in count" :key="i">
          <NCard class="grid-card-skeleton">
            <Skeleton type="rect" height="120px" />
            <div class="grid-card-content">
              <Skeleton width="80%" height="18px" />
              <Skeleton width="60%" height="14px" />
            </div>
          </NCard>
        </NGi>
      </NGrid>
    </template>

    <!-- 卡片预设（带图标） -->
    <template v-else-if="preset === 'cards'">
      <NGrid :x-gap="16" :y-gap="16" :cols="responsiveCols" responsive="screen">
        <NGi v-for="i in count" :key="i">
          <NCard class="stat-card-skeleton">
            <div class="stat-card-content">
              <Skeleton type="rect" width="56px" height="56px" />
              <div class="stat-card-info">
                <Skeleton width="80px" height="28px" />
                <Skeleton width="60px" height="14px" />
              </div>
            </div>
          </NCard>
        </NGi>
      </NGrid>
    </template>

    <!-- 详情页预设 -->
    <template v-else-if="preset === 'detail'">
      <div class="detail-skeleton">
        <!-- 主要信息区 -->
        <div class="detail-hero">
          <Skeleton type="rect" :width="isMobile ? '100%' : '200px'" height="280px" />
          <div class="detail-info">
            <Skeleton width="70%" height="32px" />
            <Skeleton width="50%" height="18px" />
            <div class="detail-meta">
              <Skeleton width="80px" height="24px" />
              <Skeleton width="80px" height="24px" />
              <Skeleton width="80px" height="24px" />
            </div>
            <Skeleton :rows="3" />
          </div>
        </div>
        <!-- 次要信息区 -->
        <div class="detail-section">
          <Skeleton width="120px" height="24px" />
          <NGrid :x-gap="12" :y-gap="12" :cols="responsiveCols" responsive="screen">
            <NGi v-for="i in 6" :key="i">
              <Skeleton type="rect" height="160px" />
            </NGi>
          </NGrid>
        </div>
      </div>
    </template>

    <!-- 表单预设 -->
    <template v-else-if="preset === 'form'">
      <NCard class="form-skeleton-card">
        <div class="form-skeleton">
          <div v-for="i in count" :key="i" class="form-field-skeleton">
            <Skeleton width="100px" height="16px" />
            <Skeleton width="100%" height="44px" />
          </div>
          <div class="form-actions">
            <Skeleton width="120px" height="44px" />
            <Skeleton width="100px" height="44px" />
          </div>
        </div>
      </NCard>
    </template>

    <!-- 表格预设 -->
    <template v-else-if="preset === 'table'">
      <NCard class="table-skeleton-card">
        <!-- 工具栏 -->
        <div class="table-toolbar-skeleton">
          <Skeleton width="200px" height="36px" />
          <div class="table-actions">
            <Skeleton width="100px" height="36px" />
            <Skeleton width="100px" height="36px" />
          </div>
        </div>
        <!-- 表头 -->
        <div class="table-header-skeleton">
          <Skeleton width="5%" height="20px" />
          <Skeleton width="30%" height="20px" />
          <Skeleton width="15%" height="20px" />
          <Skeleton width="15%" height="20px" />
          <Skeleton width="20%" height="20px" />
          <Skeleton width="15%" height="20px" />
        </div>
        <!-- 表格行 -->
        <div v-for="i in count" :key="i" class="table-row-skeleton">
          <Skeleton width="5%" height="18px" />
          <Skeleton width="30%" height="18px" />
          <Skeleton width="15%" height="18px" />
          <Skeleton width="15%" height="18px" />
          <Skeleton width="20%" height="18px" />
          <Skeleton width="15%" height="32px" />
        </div>
        <!-- 分页 -->
        <div class="table-pagination-skeleton">
          <Skeleton width="200px" height="32px" />
        </div>
      </NCard>
    </template>
  </div>
</template>

<style scoped>
.page-skeleton {
  width: 100%;
  max-width: 1200px;
  margin: 0 auto;
}

/* 页面标题 */
.page-header-skeleton {
  display: flex;
  flex-direction: column;
  gap: var(--space-2, 8px);
  margin-bottom: var(--space-6, 24px);
}

/* 列表布局 */
.list-skeleton {
  display: flex;
  flex-direction: column;
  gap: var(--space-3, 12px);
}

.list-item-skeleton {
  display: flex;
  align-items: center;
  gap: var(--space-4, 16px);
  padding: var(--space-4, 16px);
  background: var(--card-bg);
  border-radius: var(--radius-lg, 12px);
}

.list-item-content {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: var(--space-2, 8px);
}

/* 网格卡片 */
.grid-card-skeleton {
  border: none;
}

.grid-card-content {
  display: flex;
  flex-direction: column;
  gap: var(--space-2, 8px);
  margin-top: var(--space-3, 12px);
}

/* 统计卡片 */
.stat-card-skeleton {
  border: none;
}

.stat-card-content {
  display: flex;
  align-items: center;
  gap: var(--space-4, 16px);
}

.stat-card-info {
  display: flex;
  flex-direction: column;
  gap: var(--space-2, 8px);
}

/* 详情页 */
.detail-skeleton {
  display: flex;
  flex-direction: column;
  gap: var(--space-6, 24px);
}

.detail-hero {
  display: flex;
  gap: var(--space-6, 24px);
}

@media (max-width: 768px) {
  .detail-hero {
    flex-direction: column;
  }
}

.detail-info {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: var(--space-4, 16px);
}

.detail-meta {
  display: flex;
  gap: var(--space-3, 12px);
  flex-wrap: wrap;
}

.detail-section {
  display: flex;
  flex-direction: column;
  gap: var(--space-4, 16px);
}

/* 表单 */
.form-skeleton-card {
  max-width: 600px;
}

.form-skeleton {
  display: flex;
  flex-direction: column;
  gap: var(--space-5, 20px);
}

.form-field-skeleton {
  display: flex;
  flex-direction: column;
  gap: var(--space-2, 8px);
}

.form-actions {
  display: flex;
  gap: var(--space-3, 12px);
  margin-top: var(--space-4, 16px);
}

/* 表格 */
.table-skeleton-card {
  border: none;
}

.table-toolbar-skeleton {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: var(--space-4, 16px);
}

.table-actions {
  display: flex;
  gap: var(--space-3, 12px);
}

.table-header-skeleton,
.table-row-skeleton {
  display: flex;
  align-items: center;
  gap: var(--space-3, 12px);
  padding: var(--space-3, 12px) 0;
}

.table-header-skeleton {
  border-bottom: 1px solid var(--border-color, rgba(0, 0, 0, 0.08));
}

.table-row-skeleton {
  border-bottom: 1px solid var(--border-color-light, rgba(0, 0, 0, 0.04));
}

.table-pagination-skeleton {
  display: flex;
  justify-content: flex-end;
  margin-top: var(--space-4, 16px);
}

/* 移动端隐藏表格某些列 */
@media (max-width: 640px) {
  .table-header-skeleton > :nth-child(3),
  .table-header-skeleton > :nth-child(4),
  .table-row-skeleton > :nth-child(3),
  .table-row-skeleton > :nth-child(4) {
    display: none;
  }
}
</style>
