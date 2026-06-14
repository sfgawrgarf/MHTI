<script setup lang="ts">
import { ref, computed, h, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  NCard,
  NButton,
  NDataTable,
  NInput,
  NIcon,
  NPagination,
  NBreadcrumb,
  NBreadcrumbItem,
  NTag,
  useMessage,
  type DataTableColumns,
  type DataTableRowKey,
} from 'naive-ui'
import {
  FolderOutline,
  DocumentOutline,
  SearchOutline,
  AddOutline,
  ArrowBackOutline,
  SwapHorizontalOutline,
  ChevronForwardOutline,
  CloudOutline,
} from '@vicons/ionicons5'
import { filesApi } from '@/api/files'
import type { DirectoryEntry, StorageProvider, StorageLocator } from '@/api/types'
import EmptyState from '@/components/common/EmptyState.vue'
import TouchCard from '@/components/common/TouchCard.vue'
import PageSkeleton from '@/components/common/PageSkeleton.vue'
import ManualJobCreateModal from '@/components/scan/ManualJobCreateModal.vue'
import { useMobileLayout } from '@/composables/useMobileLayout'

const route = useRoute()
const router = useRouter()
const message = useMessage()
const { isMobile } = useMobileLayout()

const loading = ref(false)
const entries = ref<DirectoryEntry[]>([])
const currentPath = ref('')
const parentPath = ref<string | null>(null)
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const search = ref('')
const checkedRowKeys = ref<DataTableRowKey[]>([])
// 存储提供方跟踪：本地浏览 vs 115 网盘
const currentProvider = ref<StorageProvider>('local')
const currentFileId = ref<string | null>(null)
// 父目录的 file_id（来自后端响应），返回上级时直接使用
const parentFileId = ref<string | null>(null)

// 创建任务弹窗
const showCreateModal = ref(false)
const createTaskPath = ref('')
const createTaskLocator = ref<StorageLocator | null>(null)

// 从 URL 获取初始路径
const initPath = computed(() => (route.query.root as string) || '')
const initPage = computed(() => parseInt(route.query.page as string) || 1)

// 路径面包屑
const pathSegments = computed(() => {
  if (!currentPath.value) return []
  const segments: { name: string; path: string }[] = []
  const parts = currentPath.value.split(/[/\\]/).filter(Boolean)

  // Windows 驱动器处理
  if (currentPath.value.match(/^[A-Z]:\\/i)) {
    let accPath = ''
    parts.forEach((part, index) => {
      if (index === 0) {
        accPath = part + '\\'
        segments.push({ name: part, path: accPath })
      } else {
        accPath = accPath + part + '\\'
        segments.push({ name: part, path: accPath.slice(0, -1) })
      }
    })
  } else {
    // Unix 路径
    let accPath = ''
    parts.forEach((part) => {
      accPath = accPath + '/' + part
      segments.push({ name: part, path: accPath })
    })
  }
  return segments
})

// 格式化文件大小
const formatSize = (size: number | null) => {
  if (size === null) return '-'
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  if (size < 1024 * 1024 * 1024) return `${(size / 1024 / 1024).toFixed(1)} MB`
  return `${(size / 1024 / 1024 / 1024).toFixed(2)} GB`
}

// 格式化时间
const formatTime = (mtime: string | null) => {
  if (!mtime) return '-'
  const date = new Date(mtime)
  return date.toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
}

// 过滤后的条目
const filteredEntries = computed(() => {
  if (!search.value) return entries.value
  const keyword = search.value.toLowerCase()
  return entries.value.filter((e) => e.name.toLowerCase().includes(keyword))
})

// 表格列
const columns: DataTableColumns<DirectoryEntry> = [
  { type: 'selection' },
  {
    title: '名称',
    key: 'name',
    ellipsis: { tooltip: true },
    render: (row) =>
      h(
        'div',
        { class: 'file-name-cell', onClick: () => row.is_dir && enterDirectory(row) },
        [
          h(NIcon, {
            component: row.is_dir ? FolderOutline : DocumentOutline,
            size: 20,
            class: row.is_dir ? 'folder-icon' : 'file-icon',
          }),
          h('span', { class: 'file-name' }, row.name),
        ]
      ),
  },
  {
    title: '修改时间',
    key: 'mtime',
    width: 180,
    render: (row) => formatTime(row.mtime),
  },
  {
    title: '文件大小',
    key: 'size',
    width: 120,
    align: 'right',
    render: (row) => formatSize(row.size),
  },
  {
    title: '操作',
    key: 'actions',
    width: 80,
    render: (row) =>
      row.is_dir
        ? h(
            NButton,
            {
              size: 'small',
              quaternary: true,
              onClick: () => createTaskForFolder(row),
            },
            { icon: () => h(NIcon, { component: AddOutline }) }
          )
        : null,
  },
]

// 加载目录
const loadDirectory = async (
  path: string = '',
  p: number = 1,
  provider?: StorageProvider,
  fileId?: string | null,
) => {
  loading.value = true
  const effectiveProvider = provider ?? currentProvider.value
  const effectiveFileId = fileId !== undefined ? fileId : currentFileId.value
  try {
    const response = await filesApi.browse(path, p, pageSize.value, effectiveProvider, effectiveFileId)
    entries.value = response.entries
    currentPath.value = response.current_path
    parentPath.value = response.parent_path
    total.value = response.total
    page.value = response.page
    currentProvider.value = effectiveProvider
    // 优先用后端返回的 file_id（115 子目录必须），fallback 到请求时的值
    currentFileId.value = response.current_file_id ?? effectiveFileId ?? null
    parentFileId.value = response.parent_file_id ?? null

    // 更新 URL（仅本地路径写 URL，115 路径不持久化到 URL）
    if (currentProvider.value === 'local') {
      router.replace({
        query: {
          ...(response.current_path ? { root: response.current_path } : {}),
          ...(p > 1 ? { page: p.toString() } : {}),
        },
      })
    }
  } catch (error: any) {
    message.error(error?.response?.data?.error?.message || '加载目录失败')
    console.error(error)
  } finally {
    loading.value = false
  }
}

// 进入目录
const enterDirectory = (entry: DirectoryEntry) => {
  if (!entry.is_dir) return
  page.value = 1
  search.value = ''
  checkedRowKeys.value = []
  // 点击虚拟 115 根入口 → 切到 115 provider
  if (entry.is_virtual && entry.provider === '115') {
    loadDirectory(entry.path, 1, '115', entry.file_id ?? '0')
    return
  }
  // 同 provider 内进入子目录：115 用 file_id，本地用 path
  if (currentProvider.value === '115') {
    loadDirectory(entry.path, 1, '115', entry.file_id ?? null)
  } else {
    loadDirectory(entry.path, 1, 'local', null)
  }
}

// 返回上级
const goUp = () => {
  page.value = 1
  search.value = ''
  checkedRowKeys.value = []
  if (parentPath.value === null) {
    // 115 根的上级 → 回到本地根
    if (currentProvider.value === '115') {
      loadDirectory('', 1, 'local', null)
    }
    return
  }
  // 用后端响应里的父目录 file_id（115 必须），本地为 null 走 path
  loadDirectory(parentPath.value, 1, currentProvider.value, parentFileId.value)
}

// 跳转到路径
const goToPath = (path: string) => {
  page.value = 1
  search.value = ''
  checkedRowKeys.value = []
  // "根目录"面包屑 → 回到本地根（最顶层，含盘符 + 115 入口），无论当前 provider
  if (path === '') {
    loadDirectory('', 1, 'local', null)
    return
  }
  // 115 根层级（/115网盘）→ file_id 固定为 '0'
  if (path === '/115网盘') {
    loadDirectory('/115网盘', 1, '115', '0')
    return
  }
  // 本地其他层级：直接按 path 加载
  loadDirectory(path, 1, currentProvider.value, currentFileId.value)
}

// 返回根目录
const goToRoot = () => {
  goToPath('')
}

// 分页变化
const handlePageChange = (p: number) => {
  page.value = p
  checkedRowKeys.value = []
  loadDirectory(currentPath.value, p)
}

// 选中行变化
const handleCheckedRowKeysChange = (keys: DataTableRowKey[]) => {
  checkedRowKeys.value = keys
}

// 构造当前 provider 的 StorageLocator
const buildLocatorFor = (path: string, fileId: string | null | undefined): StorageLocator => {
  if (currentProvider.value === '115') {
    return { provider: '115', path, file_id: fileId ?? currentFileId.value, is_dir: true }
  }
  return { provider: 'local', path, is_dir: true }
}

// 为文件夹创建任务
const createTaskForFolder = (entry: DirectoryEntry) => {
  createTaskPath.value = entry.path
  createTaskLocator.value = buildLocatorFor(entry.path, entry.file_id)
  showCreateModal.value = true
}

// 批量创建任务（使用当前路径）
const createTaskForSelected = () => {
  createTaskPath.value = currentPath.value
  createTaskLocator.value = buildLocatorFor(currentPath.value, currentFileId.value)
  showCreateModal.value = true
}

// 创建任务成功
const handleCreateSuccess = () => {
  message.success('任务已创建')
}

// 行属性
const rowProps = (row: DirectoryEntry) => ({
  style: row.is_dir ? 'cursor: pointer;' : '',
  onDblclick: () => row.is_dir && enterDirectory(row),
})

// 监听路由变化（URL 仅记录本地路径，触发时强制回到本地 provider）
watch(
  () => route.query,
  () => {
    const path = (route.query.root as string) || ''
    const p = parseInt(route.query.page as string) || 1
    if (path !== currentPath.value || p !== page.value) {
      loadDirectory(path, p, 'local', null)
    }
  },
  { immediate: false }
)

// 初始加载（默认本地）
loadDirectory(initPath.value, initPage.value, 'local', null)
</script>

<template>
  <div class="files-page">
    <NCard class="main-card glass-card">
      <!-- 标题栏 -->
      <div class="page-header">
        <div class="header-left">
          <h1 class="page-title">文件管理</h1>
          <NIcon :component="SwapHorizontalOutline" size="18" class="mode-icon" />
          <router-link to="/filemanager/scan" class="mode-link">扫描模式</router-link>
        </div>
      </div>

      <!-- 面包屑导航 -->
      <div class="breadcrumb-bar">
        <NButton
          quaternary
          circle
          size="small"
          :disabled="parentPath === null && !currentPath && currentProvider !== '115'"
          @click="goUp"
        >
          <template #icon>
            <NIcon :component="ArrowBackOutline" />
          </template>
        </NButton>
        <NBreadcrumb separator="/">
          <NBreadcrumbItem @click="goToRoot">
            <span class="breadcrumb-root">根目录</span>
          </NBreadcrumbItem>
          <NBreadcrumbItem
            v-for="segment in pathSegments"
            :key="segment.path"
            @click="goToPath(segment.path)"
          >
            {{ segment.name }}
          </NBreadcrumbItem>
        </NBreadcrumb>
        <NTag
          v-if="currentProvider === '115'"
          size="small"
          type="info"
          class="provider-tag"
        >
          <template #icon>
            <NIcon :component="CloudOutline" />
          </template>
          115 网盘
        </NTag>
      </div>

      <!-- 工具栏 -->
      <div class="toolbar">
        <div class="toolbar-left">
          <NInput
            v-model:value="search"
            placeholder="关键字过滤"
            clearable
            class="search-input"
          >
            <template #prefix>
              <NIcon :component="SearchOutline" />
            </template>
          </NInput>
        </div>
        <div class="toolbar-right">
          <span class="selected-count">已选中 {{ checkedRowKeys.length }} 个条目</span>
          <NButton
            type="primary"
            :disabled="!currentPath"
            @click="createTaskForSelected"
          >
            <template #icon>
              <NIcon :component="AddOutline" />
            </template>
            创建任务
          </NButton>
        </div>
      </div>

      <!-- 加载骨架屏 -->
      <PageSkeleton v-if="loading && entries.length === 0" preset="list" :count="8" />

      <!-- 移动端文件列表 -->
      <template v-else-if="isMobile">
        <div v-if="filteredEntries.length > 0" class="mobile-file-list">
          <TouchCard
            v-for="entry in filteredEntries"
            :key="entry.path"
            clickable
            class="file-card"
            @click="entry.is_dir ? enterDirectory(entry) : undefined"
          >
            <div class="file-card-content">
              <div class="file-icon-wrapper" :class="{ 'is-folder': entry.is_dir }">
                <NIcon :component="entry.is_dir ? FolderOutline : DocumentOutline" :size="24" />
              </div>
              <div class="file-info">
                <div class="file-name-text">{{ entry.name }}</div>
                <div class="file-meta">
                  <span v-if="entry.size !== null" class="file-size">{{ formatSize(entry.size) }}</span>
                  <span v-if="entry.mtime" class="file-time">{{ formatTime(entry.mtime) }}</span>
                </div>
              </div>
            </div>
            <template #suffix>
              <div class="file-actions">
                <NButton
                  v-if="entry.is_dir"
                  size="tiny"
                  quaternary
                  circle
                  @click.stop="createTaskForFolder(entry)"
                >
                  <template #icon>
                    <NIcon :component="AddOutline" />
                  </template>
                </NButton>
                <NIcon v-if="entry.is_dir" :component="ChevronForwardOutline" class="chevron-icon" />
              </div>
            </template>
          </TouchCard>
        </div>
        <EmptyState
          v-else
          title="目录为空"
          description="当前目录没有文件或文件夹"
        />
      </template>

      <!-- 桌面端表格 -->
      <template v-else>
        <NDataTable
          v-if="filteredEntries.length > 0"
          :columns="columns"
          :data="filteredEntries"
          :loading="loading"
          :row-key="(row: DirectoryEntry) => row.path"
          :checked-row-keys="checkedRowKeys"
          :row-props="rowProps"
          @update:checked-row-keys="handleCheckedRowKeysChange"
        />
        <EmptyState
          v-else-if="!loading"
          title="目录为空"
          description="当前目录没有文件或文件夹"
        />
      </template>

      <!-- 分页 -->
      <div v-if="total > pageSize" class="pagination">
        <NPagination
          v-model:page="page"
          :page-size="pageSize"
          :item-count="total"
          @update:page="handlePageChange"
        />
      </div>
    </NCard>

    <!-- 创建任务弹窗 -->
    <ManualJobCreateModal
      v-model:show="showCreateModal"
      :initial-scan-path="createTaskPath"
      :initial-scan-locator="createTaskLocator"
      @success="handleCreateSuccess"
    />
  </div>
</template>

<style scoped>
.files-page {
  max-width: 1400px;
  margin: 0 auto;
}

/* 毛玻璃卡片 */
.glass-card {
  border: none;
  background: var(--ios-glass-bg-thick);
  backdrop-filter: blur(10px);
  -webkit-backdrop-filter: blur(10px);
}

/* 页面标题 */
.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 8px;
}

.page-title {
  font-size: 1.25rem;
  font-weight: 600;
  margin: 0;
}

.mode-icon {
  color: var(--n-text-color-3);
}

.mode-link {
  color: var(--n-text-color-3);
  text-decoration: none;
  font-size: 0.875rem;
}

.mode-link:hover {
  color: var(--n-primary-color);
}

/* 面包屑 */
.breadcrumb-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 16px;
  padding: 8px 12px;
  background: var(--n-color-modal);
  border-radius: 8px;
}

.provider-tag {
  margin-left: auto;
  flex-shrink: 0;
}

.breadcrumb-root {
  cursor: pointer;
}

:deep(.n-breadcrumb-item) {
  cursor: pointer;
}

:deep(.n-breadcrumb-item:hover) {
  color: var(--n-primary-color);
}

/* 工具栏 */
.toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
  gap: 16px;
}

.toolbar-left,
.toolbar-right {
  display: flex;
  align-items: center;
  gap: 12px;
}

.search-input {
  width: 260px;
}

.selected-count {
  color: var(--n-text-color-3);
  font-size: 0.875rem;
}

/* 文件名单元格 */
:deep(.file-name-cell) {
  display: flex;
  align-items: center;
  gap: 8px;
}

:deep(.folder-icon) {
  color: #f59e0b;
  flex-shrink: 0;
}

:deep(.file-icon) {
  color: var(--n-text-color-3);
  flex-shrink: 0;
}

:deep(.file-name) {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 分页 */
.pagination {
  display: flex;
  justify-content: flex-end;
  margin-top: 16px;
}

/* 表格样式 */
.files-page :deep(.n-data-table) {
  border-radius: 8px;
}

.files-page :deep(.n-data-table-th) {
  font-weight: 600;
}

.files-page :deep(.n-data-table-tr:hover) {
  background: var(--n-color-hover);
}

/* 按钮样式 */
.files-page :deep(.n-button--primary-type) {
  box-shadow: 0 4px 12px rgba(99, 102, 241, 0.3);
}

.files-page :deep(.n-button--primary-type:hover) {
  box-shadow: 0 6px 16px rgba(99, 102, 241, 0.4);
  transform: translateY(-1px);
}

/* 响应式 */
@media (max-width: 640px) {
  .toolbar {
    flex-direction: column;
    align-items: stretch;
  }

  .toolbar-left,
  .toolbar-right {
    width: 100%;
  }

  .search-input {
    flex: 1;
  }

  .toolbar-right {
    justify-content: space-between;
  }

  .breadcrumb-bar {
    overflow-x: auto;
  }
}

/* 移动端文件列表样式 */
.mobile-file-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.file-card {
  border-radius: 12px;
}

.file-card-content {
  display: flex;
  align-items: center;
  gap: 12px;
  flex: 1;
  min-width: 0;
}

.file-icon-wrapper {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 40px;
  height: 40px;
  border-radius: 10px;
  background: var(--n-color-modal);
  color: var(--n-text-color-3);
  flex-shrink: 0;
}

.file-icon-wrapper.is-folder {
  background: rgba(245, 158, 11, 0.15);
  color: #f59e0b;
}

.file-info {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.file-name-text {
  font-size: 15px;
  font-weight: 500;
  color: var(--n-text-color-1);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.file-meta {
  display: flex;
  align-items: center;
  gap: 12px;
  font-size: 12px;
  color: var(--n-text-color-3);
}

.file-size {
  color: var(--n-text-color-2);
}

.file-time {
  color: var(--n-text-color-3);
}

.file-actions {
  display: flex;
  align-items: center;
  gap: 4px;
}

.chevron-icon {
  color: var(--n-text-color-3);
  font-size: 18px;
}
</style>
