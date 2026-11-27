<script setup lang="ts">
/**
 * 预览步骤 - 文件列表卡片
 *
 * 纯展示。formatSize 为原 PreviewStep 内联实现逐字保留——与
 * library/utils.ts 的 formatSize 口径不同（本处 1 位小数、无 GB 上限分支、
 * falsy 判空），不合并。
 */
import { NAlert, NButton, NCard, NEmpty, NIcon, NScrollbar, NSpin, NTag } from 'naive-ui'
import { FilmOutline, FolderOutline } from '@vicons/ionicons5'
import type { DirectoryEntry } from '@/shared/types/common'

const emit = defineEmits<{ retry: [] }>()
const props = defineProps<{
  previewFiles: DirectoryEntry[]
  previewTotal: number
  error: string
  loading: boolean
}>()

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
  <NCard class="preview-card" :bordered="false">
    <template #header>
      <div class="card-header">
        <NIcon :component="FilmOutline" class="header-icon" />
        <span>文件预览</span>
        <NTag v-if="props.previewTotal > 0" type="info" size="small" round class="count-tag">
          当前目录 {{ props.previewTotal }} 个条目
        </NTag>
      </div>
    </template>
    <NSpin :show="props.loading">
      <NAlert v-if="props.error" type="error" title="文件预览失败">
        {{ props.error }}
        <NButton size="small" @click="emit('retry')">重试</NButton>
      </NAlert>
      <div v-else-if="props.previewFiles.length === 0 && !props.loading" class="empty-preview">
        <NEmpty description="暂无可预览的文件" />
      </div>
      <NScrollbar v-else style="max-height: 200px">
        <div class="file-list">
          <div
            v-for="file in props.previewFiles"
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
      <div v-if="props.previewTotal > props.previewFiles.length" class="more-hint">
        仅从当前目录前 10 个条目中展示文件夹和视频。实际任务会递归扫描并应用过滤设置。
      </div>
    </NSpin>
  </NCard>
</template>

<style scoped>
.preview-card {
  background: var(--bg-subtle);
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
  color: var(--brand-500);
}

.count-tag {
  margin-left: auto;
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
  background: var(--bg-surface);
  border-radius: 8px;
  transition: background 0.2s ease;
}

.file-item:hover {
  background: var(--bg-page);
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
  color: var(--brand-500);
  flex-shrink: 0;
}

.file-name {
  font-size: 13px;
  color: var(--text-1);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.file-size {
  font-size: 12px;
  color: var(--text-3);
  flex-shrink: 0;
  margin-left: 12px;
}

.more-hint {
  text-align: center;
  font-size: 12px;
  color: var(--text-3);
  padding: 8px 0;
}
</style>