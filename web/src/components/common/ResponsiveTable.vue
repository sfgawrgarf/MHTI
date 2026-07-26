<script setup lang="ts" generic="T extends Record<string, any>">
import { computed, type VNode } from 'vue'
import { NDataTable, NEmpty, NSpin, NPagination } from 'naive-ui'
import type { DataTableColumns, PaginationProps } from 'naive-ui'
import { useMobileLayout } from '@/composables/useMobileLayout'
import TouchCard from '@/components/common/TouchCard.vue'

/**
 * ResponsiveTable 组件
 * 响应式表格，桌面端显示表格，移动端自动转换为卡片列表
 */

export interface ResponsiveTableColumn<Row = any> {
  /** 列键名 */
  key: string
  /** 列标题 */
  title: string
  /** 是否在移动端显示 */
  showOnMobile?: boolean
  /** 是否为主要列（移动端卡片标题） */
  primary?: boolean
  /** 是否为次要列（移动端卡片副标题） */
  secondary?: boolean
  /** 自定义渲染函数 */
  render?: (row: Row, index: number) => VNode | string
  /** 列宽 */
  width?: number | string
  /** 最小宽度 */
  minWidth?: number | string
  /** 是否固定 */
  fixed?: 'left' | 'right'
  /** 是否可排序 */
  sorter?: boolean | ((a: Row, b: Row) => number)
  /** 对齐方式 */
  align?: 'left' | 'center' | 'right'
  /** 是否省略 */
  ellipsis?: boolean | { tooltip?: boolean }
}

const props = withDefaults(defineProps<{
  /** 表格数据 */
  data: T[]
  /** 列配置 */
  columns: ResponsiveTableColumn<T>[]
  /** 行键 */
  rowKey?: string | ((row: T) => string | number)
  /** 是否加载中 */
  loading?: boolean
  /** 是否显示边框 */
  bordered?: boolean
  /** 分页配置 */
  pagination?: PaginationProps | false
  /** 是否显示序号列 */
  showIndex?: boolean
  /** 空状态描述 */
  emptyDescription?: string
  /** 移动端卡片是否可点击 */
  cardClickable?: boolean
  /** 移动端卡片是否支持滑动 */
  cardSwipeable?: boolean
  /** 表格最大高度 */
  maxHeight?: string | number
  /** 是否使用虚拟滚动 */
  virtualScroll?: boolean
}>(), {
  rowKey: 'id',
  loading: false,
  bordered: false,
  pagination: false,
  showIndex: false,
  emptyDescription: '暂无数据',
  cardClickable: false,
  cardSwipeable: false,
  virtualScroll: false,
})

const emit = defineEmits<{
  'row-click': [row: T, index: number]
  'card-click': [row: T, index: number]
  'card-swipe-left': [row: T, index: number]
  'card-swipe-right': [row: T, index: number]
}>()

const { isMobile } = useMobileLayout()

// 转换为 Naive UI 表格列配置
const tableColumns = computed<DataTableColumns<T>>(() => {
  const cols: DataTableColumns<T> = []

  // 序号列
  if (props.showIndex) {
    cols.push({
      title: '#',
      key: '_index',
      width: 60,
      align: 'center',
      render: (_row, index) => index + 1,
    })
  }

  // 数据列
  for (const col of props.columns) {
    cols.push({
      title: col.title,
      key: col.key,
      width: col.width,
      minWidth: col.minWidth,
      fixed: col.fixed,
      sorter: col.sorter,
      align: col.align,
      ellipsis: col.ellipsis,
      render: col.render as any,
    })
  }

  return cols
})

// 移动端显示的列
const mobileColumns = computed(() => {
  return props.columns.filter(col => col.showOnMobile !== false)
})

// 主要列（卡片标题）
const primaryColumn = computed(() => {
  return props.columns.find(col => col.primary) ?? props.columns[0] ?? null
})

// 次要列（卡片副标题）
const secondaryColumn = computed(() => {
  return props.columns.find(col => col.secondary) ?? props.columns[1] ?? null
})

// 获取行键
function getRowKey(row: T, index: number): string | number {
  if (typeof props.rowKey === 'function') {
    return props.rowKey(row)
  }
  return row[props.rowKey] ?? index
}

// 获取单元格值
function getCellValue(row: T, col: ResponsiveTableColumn<T>, index: number): any {
  if (col.render) {
    return col.render(row, index)
  }
  return row[col.key]
}

// 卡片点击
function handleCardClick(row: T, index: number) {
  emit('card-click', row, index)
}

// 行点击
function handleRowClick(row: T, index: number) {
  emit('row-click', row, index)
}

// 卡片左滑
function handleCardSwipeLeft(row: T, index: number) {
  emit('card-swipe-left', row, index)
}

// 卡片右滑
function handleCardSwipeRight(row: T, index: number) {
  emit('card-swipe-right', row, index)
}
</script>

<template>
  <div class="responsive-table">
    <!-- 桌面端：表格视图 -->
    <template v-if="!isMobile">
      <NDataTable
        :columns="tableColumns"
        :data="data"
        :row-key="rowKey as any"
        :loading="loading"
        :bordered="bordered"
        :pagination="pagination"
        :max-height="maxHeight"
        :virtual-scroll="virtualScroll"
        :row-props="(row: T, index: number) => ({
          style: cardClickable ? 'cursor: pointer' : undefined,
          onClick: () => handleRowClick(row, index),
        })"
        class="ios-table"
      />
    </template>

    <!-- 移动端：卡片列表视图 -->
    <template v-else>
      <NSpin :show="loading">
        <!-- 空状态 -->
        <NEmpty v-if="data.length === 0" :description="emptyDescription" class="empty-state" />

        <!-- 卡片列表 -->
        <div v-else class="card-list">
          <TouchCard
            v-for="(row, index) in data"
            :key="getRowKey(row, index)"
            :clickable="cardClickable"
            :swipeable="cardSwipeable"
            size="medium"
            class="data-card"
            @click="handleCardClick(row, index)"
            @swipe-left="handleCardSwipeLeft(row, index)"
            @swipe-right="handleCardSwipeRight(row, index)"
          >
            <!-- 卡片头部：主要信息 -->
            <div class="card-header">
              <div v-if="primaryColumn" class="card-primary">
                <component
                  :is="getCellValue(row, primaryColumn, index)"
                  v-if="typeof getCellValue(row, primaryColumn, index) === 'object'"
                />
                <span v-else>{{ getCellValue(row, primaryColumn, index) }}</span>
              </div>
              <div v-if="secondaryColumn" class="card-secondary">
                <component
                  :is="getCellValue(row, secondaryColumn, index)"
                  v-if="typeof getCellValue(row, secondaryColumn, index) === 'object'"
                />
                <span v-else>{{ getCellValue(row, secondaryColumn, index) }}</span>
              </div>
            </div>

            <!-- 卡片内容：其他字段 -->
            <div class="card-body">
              <template v-for="col in mobileColumns" :key="col.key">
                <div
                  v-if="!col.primary && !col.secondary"
                  class="card-field"
                >
                  <span class="field-label">{{ col.title }}</span>
                  <span class="field-value">
                    <component
                      :is="getCellValue(row, col, index)"
                      v-if="typeof getCellValue(row, col, index) === 'object'"
                    />
                    <template v-else>{{ getCellValue(row, col, index) }}</template>
                  </span>
                </div>
              </template>
            </div>

            <!-- 操作区域插槽 -->
            <template v-if="$slots.cardActions" #footer>
              <div class="card-actions">
                <slot name="cardActions" :row="row" :index="index" />
              </div>
            </template>

            <!-- 滑动操作插槽 -->
            <template v-if="$slots.swipeLeft" #swipe-left>
              <slot name="swipeLeft" :row="row" :index="index" />
            </template>

            <template v-if="$slots.swipeRight" #swipe-right>
              <slot name="swipeRight" :row="row" :index="index" />
            </template>
          </TouchCard>
        </div>

        <!-- 移动端分页 -->
        <div v-if="pagination && data.length > 0" class="mobile-pagination">
          <NPagination
            v-bind="pagination"
            :page-slot="5"
            :show-size-picker="false"
            :show-quick-jumper="false"
          />
        </div>
      </NSpin>
    </template>
  </div>
</template>

<style scoped>
.responsive-table {
  width: 100%;
}

/* iOS 风格表格 */
.ios-table {
  --n-border-radius: 12px;
}

.ios-table :deep(.n-data-table-wrapper) {
  border-radius: 12px;
}

.ios-table :deep(.n-data-table-th) {
  font-weight: 600;
  font-size: 13px;
  color: var(--color-text-secondary, #8e8e93);
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.ios-table :deep(.n-data-table-td) {
  font-size: 15px;
}

.ios-table :deep(.n-data-table-tr:hover .n-data-table-td) {
  background: var(--color-fill-secondary, rgba(0, 122, 255, 0.05));
}

/* 卡片列表 */
.card-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.data-card {
  --n-padding-top: 16px;
  --n-padding-bottom: 16px;
  --n-padding-left: 16px;
}

/* 卡片头部 */
.card-header {
  margin-bottom: 12px;
}

.card-primary {
  font-size: 16px;
  font-weight: 600;
  color: var(--color-text-primary, #1c1c1e);
  line-height: 1.4;
}

:global(.dark) .card-primary {
  color: #fff;
}

.card-secondary {
  font-size: 14px;
  color: var(--color-text-secondary, #8e8e93);
  margin-top: 4px;
}

/* 卡片内容 */
.card-body {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.card-field {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 14px;
}

.field-label {
  color: var(--color-text-secondary, #8e8e93);
  flex-shrink: 0;
}

.field-value {
  color: var(--color-text-primary, #1c1c1e);
  text-align: right;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

:global(.dark) .field-value {
  color: #fff;
}

/* 卡片操作区域 */
.card-actions {
  display: flex;
  gap: 8px;
  padding-top: 12px;
  border-top: 1px solid var(--border-color-light, rgba(0, 0, 0, 0.06));
}

/* 空状态 */
.empty-state {
  padding: 48px 16px;
}

/* 移动端分页 */
.mobile-pagination {
  display: flex;
  justify-content: center;
  padding: 16px 0;
}

.mobile-pagination :deep(.n-pagination) {
  --n-item-size: 36px;
  --n-button-icon-size: 16px;
}
</style>
