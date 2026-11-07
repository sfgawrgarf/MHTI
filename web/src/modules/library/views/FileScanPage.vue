<script setup lang="ts">
import { ref } from 'vue'
import { NButton, NIcon, NInput, NPagination, useMessage } from 'naive-ui'
import { AddOutline, FolderOutline, SearchOutline, SwapHorizontalOutline } from '@vicons/ionicons5'
import type { ScannedFile } from '@/modules/library/types'
import { useFileScan } from '@/modules/library/hooks/useFileScan'
import FolderBrowser from '@/shared/components/business/FolderBrowser.vue'
import EmptyState from '@/shared/components/base/EmptyState.vue'
import PageContainer from '@/shared/components/base/PageContainer.vue'
import ScanResultTable from '@/modules/library/components/ScanResultTable.vue'
import { ManualJobCreateModal } from '@/modules/scrape'

const message = useMessage()

// 扫描（状态/执行/客户端分页/选中行）
const {
  loading,
  scanPath,
  scanLocator,
  scannedFiles,
  total,
  page,
  pageSize,
  checkedRowKeys,
  paginatedFiles,
  handleScan,
  handleFolderLocator,
  handlePageChange,
  handleCheckedRowKeysChange,
} = useFileScan()

const showFolderBrowser = ref(false)

// 创建任务弹窗
const showCreateModal = ref(false)
const createTaskPath = ref('')
const selectedFiles = ref<ScannedFile[]>([])

// 选择文件夹
const handleFolderSelect = (path: string) => {
  scanPath.value = path
  showFolderBrowser.value = false
}

// 为单个文件创建任务（使用扫描路径）
const createTaskForFile = (file: ScannedFile) => {
  selectedFiles.value = [file]
  createTaskPath.value = scanPath.value
  showCreateModal.value = true
}

// 批量创建任务（使用扫描路径）
const createTaskForSelected = () => {
  const selection = scannedFiles.value.filter(file => checkedRowKeys.value.includes(file.path))
  if (!selection.length) { message.warning('请先选择文件'); return }
  if (selection.length > 1000) { message.warning('一次最多选择 1000 个文件'); return }
  selectedFiles.value = selection
  createTaskPath.value = scanPath.value
  showCreateModal.value = true
}

// 创建任务成功
const handleCreateSuccess = () => {
  message.success('任务已创建')
}
</script>

<template>
  <div class="file-scan-page">
    <PageContainer>
      <!-- 标题由 PageContainer 取自路由 meta；此处只放模式切换入口 -->
      <template #actions>
        <router-link to="/files" class="mode-link">
          <NIcon :component="SwapHorizontalOutline" :size="15" />
          文件树模式
        </router-link>
      </template>

      <!-- 工具栏 -->
      <div class="toolbar">
        <div class="toolbar-left">
          <div class="scan-input-group">
            <NInput
              v-model:value="scanPath"
              placeholder="请输入扫描路径"
              clearable
              class="scan-input"
              @keyup.enter="handleScan"
            >
              <template #prefix>
                <NIcon :component="SearchOutline" />
              </template>
            </NInput>
            <NButton aria-label="选择扫描目录" @click="showFolderBrowser = true">
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
            :disabled="!scanPath || checkedRowKeys.length === 0"
            @click="createTaskForSelected"
          >
            <template #icon>
              <NIcon :component="AddOutline" />
            </template>
            为选中文件创建任务
          </NButton>
        </div>
      </div>

      <!-- 表格 -->
      <ScanResultTable
        v-if="scannedFiles.length > 0 || loading"
        :files="paginatedFiles"
        :loading="loading"
        :checked-row-keys="checkedRowKeys"
        @update:checked-row-keys="handleCheckedRowKeysChange"
        @create="createTaskForFile"
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
    </PageContainer>

    <!-- 文件夹选择弹窗 -->
    <FolderBrowser
      v-model:show="showFolderBrowser"
      v-model="scanPath"
      title="选择文件夹"
      @select="handleFolderSelect"
      @select-locator="handleFolderLocator"
    />

    <!-- 创建任务弹窗 -->
    <ManualJobCreateModal
      v-model:show="showCreateModal"
      :initial-scan-path="createTaskPath"
      :initial-scan-locator="scanLocator"
      :selected-files="selectedFiles"
      @success="handleCreateSuccess"
    />
  </div>
</template>

<style scoped>
.file-scan-page {
  display: flex;
  flex-direction: column;
}

.mode-link {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  font-size: var(--text-sm);
  color: var(--text-2);
  transition: color var(--duration-fast) var(--ease-in-out);
}

.mode-link:hover {
  color: var(--brand-500);
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
  color: var(--text-3);
  font-size: 0.875rem;
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
  background: var(--bg-hover);
}

/* 按钮样式 */
.file-scan-page :deep(.n-button--primary-type) {
  box-shadow: 0 4px 12px rgb(var(--brand-rgb) / 30%);
}

.file-scan-page :deep(.n-button--primary-type:hover) {
  box-shadow: 0 6px 16px rgb(var(--brand-rgb) / 40%);
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
