<script setup lang="ts">
import { computed } from 'vue'
import {
  NDescriptions,
  NDescriptionsItem,
  NTag,
  NIcon,
  NSpin,
  NEmpty,
  NScrollbar,
  NCard,
} from 'naive-ui'
import {
  FolderOutline,
  DocumentOutline,
  LinkOutline,
  CheckmarkCircleOutline,
  CloseCircleOutline,
  FilmOutline,
} from '@vicons/ionicons5'
import { LinkMode } from '@/api/types'
import type { DirectoryEntry } from '@/api/types'

const props = defineProps<{
  formData: {
    scan_path: string
    target_folder: string
    metadata_dir: string
    link_mode: LinkMode
    delete_empty_parent: boolean
    download_poster: boolean
    download_backdrop: boolean
    download_thumbnail: boolean
    generate_nfo: boolean
    process_subtitle: boolean
    overwrite_existing: boolean
  }
  previewFiles: DirectoryEntry[]
  previewTotal: number
  loading: boolean
}>()

// 链接模式显示名称
const linkModeLabel = computed(() => {
  const labels: Record<LinkMode, string> = {
    [LinkMode.HARDLINK]: '硬链接',
    [LinkMode.COPY]: '复制',
    [LinkMode.MOVE]: '移动',
    [LinkMode.SYMLINK]: '软链接',
  }
  return labels[props.formData.link_mode] || props.formData.link_mode
})

// 开关状态渲染
const renderSwitch = (value: boolean, label: string) => {
  return { value, label }
}

// 配置摘要
const configSummary = computed(() => [
  renderSwitch(props.formData.download_poster, '海报'),
  renderSwitch(props.formData.download_backdrop, '背景图'),
  renderSwitch(props.formData.download_thumbnail, '缩略图'),
  renderSwitch(props.formData.generate_nfo, 'NFO'),
  renderSwitch(props.formData.process_subtitle, '字幕'),
])

// 文件图标
const getFileIcon = (entry: DirectoryEntry) => {
  if (entry.is_dir) return FolderOutline
  return FilmOutline
}

// 格式化文件大小
const formatSize = (bytes?: number | null) => {
  if (!bytes) return '-'
  const units = ['B', 'KB', 'MB', 'GB']
  let size = bytes
  let unitIndex = 0
  while (size >= 1024 && unitIndex < units.length - 1) {
    size /= 1024
    unitIndex++
  }
  return `${size.toFixed(1)} ${units[unitIndex]}`
}
</script>

<template>
  <div class="preview-step">
    <!-- 配置摘要 -->
    <NCard class="summary-card" :bordered="false">
      <template #header>
        <div class="card-header">
          <NIcon :component="CheckmarkCircleOutline" class="header-icon" />
          <span>配置摘要</span>
        </div>
      </template>
      <NDescriptions :column="1" label-placement="left" :label-style="{ width: '100px' }">
        <NDescriptionsItem label="刮削路径">
          <div class="path-value">
            <NIcon :component="FolderOutline" />
            <span>{{ formData.scan_path || '-' }}</span>
          </div>
        </NDescriptionsItem>
        <NDescriptionsItem label="整理目录">
          <div class="path-value">
            <NIcon :component="FolderOutline" />
            <span>{{ formData.target_folder || '-' }}</span>
          </div>
        </NDescriptionsItem>
        <NDescriptionsItem v-if="formData.metadata_dir" label="元数据目录">
          <div class="path-value">
            <NIcon :component="DocumentOutline" />
            <span>{{ formData.metadata_dir }}</span>
          </div>
        </NDescriptionsItem>
        <NDescriptionsItem label="整理模式">
          <NTag type="info" size="small" round>
            <template #icon>
              <NIcon :component="LinkOutline" />
            </template>
            {{ linkModeLabel }}
          </NTag>
        </NDescriptionsItem>
        <NDescriptionsItem label="下载选项">
          <div class="config-tags">
            <NTag
              v-for="item in configSummary"
              :key="item.label"
              :type="item.value ? 'success' : 'default'"
              size="small"
              round
            >
              <template #icon>
                <NIcon :component="item.value ? CheckmarkCircleOutline : CloseCircleOutline" />
              </template>
              {{ item.label }}
            </NTag>
          </div>
        </NDescriptionsItem>
      </NDescriptions>
    </NCard>

    <!-- 文件预览 -->
    <NCard class="preview-card" :bordered="false">
      <template #header>
        <div class="card-header">
          <NIcon :component="FilmOutline" class="header-icon" />
          <span>文件预览</span>
          <NTag v-if="previewTotal > 0" type="info" size="small" round class="count-tag">
            共 {{ previewTotal }} 个
          </NTag>
        </div>
      </template>
      <NSpin :show="loading">
        <div v-if="previewFiles.length === 0 && !loading" class="empty-preview">
          <NEmpty description="暂无可预览的文件" />
        </div>
        <NScrollbar v-else style="max-height: 200px">
          <div class="file-list">
            <div
              v-for="file in previewFiles"
              :key="file.name"
              class="file-item"
            >
              <div class="file-info">
                <NIcon :component="getFileIcon(file)" class="file-icon" />
                <span class="file-name">{{ file.name }}</span>
              </div>
              <span class="file-size">{{ formatSize(file.size) }}</span>
            </div>
          </div>
        </NScrollbar>
        <div v-if="previewTotal > previewFiles.length" class="more-hint">
          还有 {{ previewTotal - previewFiles.length }} 个文件...
        </div>
      </NSpin>
    </NCard>

    <!-- 警告提示 -->
    <div v-if="formData.overwrite_existing" class="warning-box">
      <NIcon :component="CloseCircleOutline" class="warning-icon" />
      <span>已开启覆盖模式，现有文件可能被覆盖</span>
    </div>
  </div>
</template>

<style scoped>
.preview-step {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.summary-card,
.preview-card {
  background: var(--ios-bg-tertiary);
  border-radius: 16px;
}

.card-header {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 600;
}

.header-icon {
  font-size: 18px;
  color: var(--ios-blue);
}

.count-tag {
  margin-left: auto;
}

.path-value {
  display: flex;
  align-items: center;
  gap: 6px;
  font-family: 'SF Mono', Monaco, monospace;
  font-size: 13px;
  color: var(--ios-text-secondary);
  word-break: break-all;
}

.path-value :deep(.n-icon) {
  flex-shrink: 0;
  color: var(--ios-blue);
}

.config-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.empty-preview {
  padding: 24px 0;
}

.file-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.file-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 12px;
  background: var(--ios-bg-secondary);
  border-radius: 8px;
  transition: background 0.2s ease;
}

.file-item:hover {
  background: var(--ios-bg-primary);
}

.file-info {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 1;
  min-width: 0;
}

.file-icon {
  font-size: 16px;
  color: var(--ios-blue);
  flex-shrink: 0;
}

.file-name {
  font-size: 13px;
  color: var(--ios-text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.file-size {
  font-size: 12px;
  color: var(--ios-text-tertiary);
  flex-shrink: 0;
  margin-left: 12px;
}

.more-hint {
  text-align: center;
  font-size: 12px;
  color: var(--ios-text-tertiary);
  padding: 8px 0;
}

.warning-box {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 16px;
  background: rgba(255, 59, 48, 0.1);
  border: 1px solid var(--ios-red);
  border-radius: 12px;
  color: var(--ios-red);
  font-size: 13px;
}

.warning-icon {
  font-size: 18px;
}

:deep(.n-descriptions-table-content) {
  padding: 8px 0;
}

:deep(.n-descriptions-table-header) {
  font-weight: 500;
  color: var(--ios-text-secondary);
}
</style>
