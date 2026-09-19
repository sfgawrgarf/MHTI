<script setup lang="ts">
import { ref, computed, h } from 'vue'
import {
  NCard,
  NButton,
  NDataTable,
  NInput,
  NIcon,
  NPagination,
  useMessage,
  type DataTableColumns,
  type DataTableRowKey,
} from 'naive-ui'
import {
  DocumentOutline,
  SearchOutline,
  AddOutline,
  FolderOutline,
  SwapHorizontalOutline,
} from '@vicons/ionicons5'
import { filesApi } from '@/api/files'
import type { ManualJobScanSource, ScannedFile, StorageLocator } from '@/api/types'
import FolderBrowser from '@/components/scan/FolderBrowser.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import ManualJobCreateModal from '@/components/scan/ManualJobCreateModal.vue'

const message = useMessage()

const loading = ref(false)
const scanPath = ref('')
const scanLocator = ref<StorageLocator | null>(null)
const scannedFiles = ref<ScannedFile[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const checkedRowKeys = ref<DataTableRowKey[]>([])
const showFolderBrowser = ref(false)

// 创建任务弹窗
const showCreateModal = ref(false)
const createTaskPath = ref('')
const createTaskSources = ref<ManualJobScanSource[]>([])

// 格式化文件大小
const formatSize = (size: number) => {
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

// 分页后的文件列表
const paginatedFiles = computed(() => {
  const start = (page.value - 1) * pageSize.value
  const end = start + pageSize.value
  return scannedFiles.value.slice(start, end)
})

// 表格列
const columns: DataTableColumns<ScannedFile> = [
  { type: 'selection' },
  {
    title: '名称',
    key: 'filename',
    ellipsis: { tooltip: true },
    render: (row) =>
      h('div', { class: 'file-name-cell' }, [
        h(NIcon, {
          component: DocumentOutline,
          size: 20,
          class: 'file-icon',
        }),
        h('span', { class: 'file-name' }, row.filename),
      ]),
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
      h(
        NButton,
        {
          size: 'small',
          quaternary: true,
          onClick: () => createTaskForFile(row),
        },
        { icon: () => h(NIcon, { component: AddOutline }) }
      ),
  },
]

// 扫描目录
const handleScan = async () => {
  if (!scanPath.value) {
    message.warning('请输入扫描路径')
    return
  }

  loading.value = true
  checkedRowKeys.value = []
  page.value = 1

  try {
    const response = await filesApi.scan(scanPath.value, true, scanLocator.value)
    scannedFiles.value = response.files
    total.value = response.total_files

    if (response.total_files === 0) {
      message.info('未找到视频文件')
    } else {
      message.success(`找到 ${response.total_files} 个视频文件`)
    }
  } catch (error) {
    message.error('扫描失败')
    console.error(error)
  } finally {
    loading.value = false
  }
}

// 选择文件夹
const handleFolderSelect = (path: string) => {
  scanPath.value = path
  scannedFiles.value = []
  checkedRowKeys.value = []
  total.value = 0
  showFolderBrowser.value = false
}

// 接收文件夹选择器的 locator（115 等云端目录）
const handleFolderLocator = (locator: StorageLocator) => {
  scanLocator.value = locator
}

const handleScanPathInput = (path: string) => {
  scanPath.value = path
  scanLocator.value = null
  scannedFiles.value = []
  checkedRowKeys.value = []
  total.value = 0
}

// 分页变化
const handlePageChange = (p: number) => {
  page.value = p
  checkedRowKeys.value = []
}

// 选中行变化
const handleCheckedRowKeysChange = (keys: DataTableRowKey[]) => {
  checkedRowKeys.value = keys
}

const createSourceForFile = (file: ScannedFile): ManualJobScanSource => ({
  path: file.path,
  locator: scanLocator.value?.provider === '115'
    ? {
        provider: '115',
        path: file.path,
        file_id: file.file_id,
        parent_id: file.parent_id,
        is_dir: false,
      }
    : { provider: 'local', path: file.path, is_dir: false },
})

// 为单个文件创建任务
const createTaskForFile = (file: ScannedFile) => {
  createTaskPath.value = file.path
  createTaskSources.value = [createSourceForFile(file)]
  showCreateModal.value = true
}

// 为当前勾选的文件分别创建任务
const createTaskForSelected = () => {
  const selectedKeys = new Set(checkedRowKeys.value.map(String))
  const selectedFiles = scannedFiles.value.filter((file) => selectedKeys.has(file.path))
  if (selectedFiles.length === 0) {
    message.warning('请先选择要创建任务的文件')
    return
  }
  createTaskPath.value = selectedFiles[0]?.path ?? ''
  createTaskSources.value = selectedFiles.map(createSourceForFile)
  showCreateModal.value = true
}

// 创建任务成功
const handleCreateSuccess = () => {
  message.success('任务已创建')
}
</script>

<template>
  <div class="file-scan-page">
    <NCard class="main-card glass-card">
      <!-- 标题栏 -->
      <div class="page-header">
        <div class="header-left">
          <h1 class="page-title">文件扫描</h1>
          <NIcon :component="SwapHorizontalOutline" size="18" class="mode-icon" />
          <router-link to="/files" class="mode-link">文件树模式</router-link>
        </div>
      </div>

      <!-- 工具栏 -->
      <div class="toolbar">
        <div class="toolbar-left">
          <div class="scan-input-group">
            <NInput
              :value="scanPath"
              placeholder="请输入扫描路径"
              clearable
              class="scan-input"
              @update:value="handleScanPathInput"
              @keyup.enter="handleScan"
            >
              <template #prefix>
                <NIcon :component="SearchOutline" />
              </template>
            </NInput>
            <NButton @click="showFolderBrowser = true">
              <template #icon>
                <NIcon :component="FolderOutline" />
              </template>
            </NButton>
          </div>
          <NButton
            type="primary"
            :loading="loading"
            :disabled="!scanPath"
            @click="handleScan"
          >
            扫描
          </NButton>
        </div>
        <div class="toolbar-right">
          <span class="selected-count">已选中 {{ checkedRowKeys.length }} 个条目</span>
          <NButton
            type="primary"
            :disabled="checkedRowKeys.length === 0"
            @click="createTaskForSelected"
          >
            <template #icon>
              <NIcon :component="AddOutline" />
            </template>
            创建任务
          </NButton>
        </div>
      </div>

      <!-- 表格 -->
      <NDataTable
        v-if="scannedFiles.length > 0 || loading"
        :columns="columns"
        :data="paginatedFiles"
        :loading="loading"
        :row-key="(row: ScannedFile) => row.path"
        :checked-row-keys="checkedRowKeys"
        @update:checked-row-keys="handleCheckedRowKeysChange"
      />

      <!-- 空状态 -->
      <EmptyState
        v-else-if="!loading"
        title="暂无扫描结果"
        description="输入路径并点击扫描按钮开始扫描视频文件"
      />

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

    <!-- 文件夹选择弹窗 -->
    <FolderBrowser
      v-model:show="showFolderBrowser"
      v-model="scanPath"
      :locator="scanLocator"
      title="选择文件夹"
      @select="handleFolderSelect"
      @select-locator="handleFolderLocator"
    />

    <!-- 创建任务弹窗 -->
    <ManualJobCreateModal
      v-model:show="showCreateModal"
      :initial-scan-path="createTaskPath"
      :initial-scan-sources="createTaskSources"
      @success="handleCreateSuccess"
    />
  </div>
</template>

<style scoped>
.file-scan-page {
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

/* 工具栏 */
.toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
  gap: 16px;
  flex-wrap: wrap;
}

.toolbar-left,
.toolbar-right {
  display: flex;
  align-items: center;
  gap: 12px;
}

.scan-input-group {
  display: flex;
  gap: 4px;
}

.scan-input {
  width: 320px;
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
.file-scan-page :deep(.n-data-table) {
  border-radius: 8px;
}

.file-scan-page :deep(.n-data-table-th) {
  font-weight: 600;
}

.file-scan-page :deep(.n-data-table-tr:hover) {
  background: var(--n-color-hover);
}

/* 按钮样式 */
.file-scan-page :deep(.n-button--primary-type) {
  box-shadow: 0 4px 12px rgba(99, 102, 241, 0.3);
}

.file-scan-page :deep(.n-button--primary-type:hover) {
  box-shadow: 0 6px 16px rgba(99, 102, 241, 0.4);
  transform: translateY(-1px);
}

/* 响应式 */
@media (max-width: 768px) {
  .toolbar {
    flex-direction: column;
    align-items: stretch;
  }

  .toolbar-left,
  .toolbar-right {
    width: 100%;
    flex-wrap: wrap;
  }

  .scan-input-group {
    flex: 1;
  }

  .scan-input {
    width: 100%;
    flex: 1;
  }

  .toolbar-right {
    justify-content: space-between;
  }
}
</style>
