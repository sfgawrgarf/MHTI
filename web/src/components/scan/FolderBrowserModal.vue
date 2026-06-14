<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import {
  NModal,
  NCard,
  NInput,
  NButton,
  NSpace,
  NIcon,
  NSpin,
  NTag,
} from 'naive-ui'
import {
  FolderOutline,
  ArrowUpOutline,
  SearchOutline,
  CloseOutline,
  CheckmarkOutline,
  CloudOutline,
} from '@vicons/ionicons5'
import { filesApi } from '@/api/files'
import type { DirectoryEntry, StorageLocator, StorageProvider } from '@/api/types'

const props = defineProps<{
  show: boolean
  title?: string
}>()

const emit = defineEmits<{
  (e: 'update:show', value: boolean): void
  (e: 'confirm', path: string): void
  (e: 'confirmLocator', locator: StorageLocator): void
}>()

const loading = ref(false)
const currentPath = ref('')
const parentPath = ref<string | null>(null)
const entries = ref<DirectoryEntry[]>([])
const filter = ref('')
const selectedPath = ref('')
// 存储提供方跟踪
const currentProvider = ref<StorageProvider>('local')
const currentFileId = ref<string | null>(null)
// 父目录的 file_id（来自后端响应），返回上级时直接使用
const parentFileId = ref<string | null>(null)

// 过滤后的目录列表
const filteredDirs = computed(() => {
  const dirs = entries.value.filter((e) => e.is_dir)
  if (!filter.value) return dirs
  const keyword = filter.value.toLowerCase()
  return dirs.filter((d) => d.name.toLowerCase().includes(keyword))
})

// 路径分段（面包屑）
const pathSegments = computed(() => {
  if (!currentPath.value) return []
  const parts = currentPath.value.split('/').filter(Boolean)
  const segments: { name: string; path: string }[] = []
  let path = ''
  for (const part of parts) {
    path += '/' + part
    segments.push({ name: part, path })
  }
  return segments
})

// 当前 provider 标签
const providerTag = computed(() => {
  if (currentProvider.value === '115') return { label: '115 网盘', type: 'info' as const }
  return { label: '本地', type: 'default' as const }
})

// 加载目录
const loadDirectory = async (
  path: string = '',
  provider?: StorageProvider,
  fileId?: string | null,
) => {
  loading.value = true
  const effectiveProvider = provider ?? currentProvider.value
  const effectiveFileId = fileId !== undefined ? fileId : currentFileId.value
  try {
    const response = await filesApi.browse(path, 1, 20, effectiveProvider, effectiveFileId)
    currentPath.value = response.current_path
    parentPath.value = response.parent_path
    entries.value = response.entries
    selectedPath.value = response.current_path
    currentProvider.value = effectiveProvider
    // 优先用后端返回的 file_id（115 子目录必须），fallback 到请求时的值
    currentFileId.value = response.current_file_id ?? effectiveFileId ?? null
    parentFileId.value = response.parent_file_id ?? null
  } catch (error) {
    console.error('加载目录失败:', error)
  } finally {
    loading.value = false
  }
}

// 进入目录
const enterDirectory = (entry: DirectoryEntry) => {
  if (!entry.is_dir) return
  // 点击虚拟 115 根入口 → 切到 115 provider
  if (entry.is_virtual && entry.provider === '115') {
    loadDirectory(entry.path, '115', entry.file_id ?? '0')
    return
  }
  if (currentProvider.value === '115') {
    loadDirectory(entry.path, '115', entry.file_id ?? null)
  } else {
    loadDirectory(entry.path, 'local', null)
  }
}

// 返回上级
const goUp = () => {
  if (parentPath.value === null) {
    // 115 根的上级 → 回到本地根
    if (currentProvider.value === '115') {
      loadDirectory('', 'local', null)
    }
    return
  }
  // 用后端响应里的父目录 file_id（115 必须），本地为 null 走 path
  loadDirectory(parentPath.value, currentProvider.value, parentFileId.value)
}

// 跳转到指定路径（面包屑用）
const goToPath = (path: string) => {
  // "根目录"面包屑 → 回到本地根（最顶层，含盘符 + 115 入口），无论当前 provider
  if (path === '') {
    loadDirectory('', 'local', null)
    return
  }
  // 115 根层级（/115网盘）→ file_id 固定为 '0'
  if (path === '/115网盘') {
    loadDirectory('/115网盘', '115', '0')
    return
  }
  // 本地其他层级：直接按 path 加载
  loadDirectory(path, currentProvider.value, currentFileId.value)
}

// 关闭弹窗
const handleClose = () => {
  emit('update:show', false)
}

// 构造当前目录的 StorageLocator
const buildLocator = (): StorageLocator => {
  if (currentProvider.value === '115') {
    return {
      provider: '115',
      path: currentPath.value,
      file_id: currentFileId.value,
      is_dir: true,
    }
  }
  return {
    provider: 'local',
    path: currentPath.value,
    is_dir: true,
  }
}

// 确认选择
const handleConfirm = () => {
  if (selectedPath.value) {
    emit('confirm', selectedPath.value)
    emit('confirmLocator', buildLocator())
    handleClose()
  }
}

// 监听显示状态，打开时加载根目录
watch(
  () => props.show,
  (show) => {
    if (show) {
      filter.value = ''
      currentProvider.value = 'local'
      currentFileId.value = null
      parentFileId.value = null
      loadDirectory('', 'local', null)
    }
  }
)
</script>

<template>
  <NModal
    :show="show"
    :mask-closable="false"
    transform-origin="center"
    @update:show="emit('update:show', $event)"
  >
    <NCard class="browser-modal" :bordered="false">
      <!-- 头部 -->
      <template #header>
        <NSpace align="center" size="small">
          <span class="header-title">{{ title || '选择文件夹' }}</span>
          <NTag size="small" :type="providerTag.type">{{ providerTag.label }}</NTag>
        </NSpace>
      </template>
      <template #header-extra>
        <NButton quaternary circle size="small" @click="handleClose">
          <template #icon>
            <NIcon :component="CloseOutline" />
          </template>
        </NButton>
      </template>

      <!-- 内容 -->
      <div class="browser-body">
        <!-- 面包屑导航 -->
        <div class="breadcrumb">
          <span class="breadcrumb-item root" @click="goToPath('')">
            <NIcon :component="FolderOutline" :size="14" />
            根目录
          </span>
          <template v-for="(seg, idx) in pathSegments" :key="seg.path">
            <span class="breadcrumb-sep">/</span>
            <span
              class="breadcrumb-item"
              :class="{ active: idx === pathSegments.length - 1 }"
              @click="goToPath(seg.path)"
            >
              {{ seg.name }}
            </span>
          </template>
        </div>

        <!-- 搜索过滤 -->
        <NInput
          v-model:value="filter"
          placeholder="搜索文件夹..."
          clearable
          class="search-input"
        >
          <template #prefix>
            <NIcon :component="SearchOutline" />
          </template>
        </NInput>

        <!-- 目录列表 -->
        <NSpin :show="loading">
          <div class="folder-list">
            <!-- 返回上级 -->
            <div
              v-if="parentPath !== null || currentProvider === '115'"
              class="folder-item back"
              @click="goUp"
            >
              <div class="folder-icon back-icon">
                <NIcon :component="ArrowUpOutline" :size="18" />
              </div>
              <span class="folder-name">返回上级</span>
            </div>

            <!-- 空目录 -->
            <div v-if="filteredDirs.length === 0 && !loading" class="empty-state">
              <NIcon :component="FolderOutline" :size="32" />
              <span>目录为空</span>
            </div>

            <!-- 目录项 -->
            <div
              v-for="entry in filteredDirs"
              :key="entry.path"
              class="folder-item"
              @click="enterDirectory(entry)"
            >
              <div class="folder-icon" :class="{ 'cloud-icon': entry.is_virtual }">
                <NIcon :component="entry.is_virtual ? CloudOutline : FolderOutline" :size="18" />
              </div>
              <span class="folder-name">{{ entry.name }}</span>
              <NIcon :component="CheckmarkOutline" class="enter-icon" :size="16" />
            </div>
          </div>
        </NSpin>

        <!-- 当前选择 -->
        <div class="current-path">
          <span class="path-label">已选择:</span>
          <span class="path-value">{{ selectedPath || '未选择' }}</span>
        </div>
      </div>

      <!-- 底部 -->
      <template #footer>
        <NSpace justify="end">
          <NButton @click="handleClose">取消</NButton>
          <NButton type="primary" :disabled="!selectedPath" @click="handleConfirm">
            确认选择
          </NButton>
        </NSpace>
      </template>
    </NCard>
  </NModal>
</template>

<style scoped>
.browser-modal {
  width: 600px;
  max-width: 95vw;
  border-radius: 16px;
  background: var(--ios-glass-bg-thick);
  backdrop-filter: blur(20px);
  -webkit-backdrop-filter: blur(20px);
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.15);
}

.header-title {
  font-size: 18px;
  font-weight: 600;
}

.browser-body {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

/* 面包屑 */
.breadcrumb {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 4px;
  padding: 10px 12px;
  background: var(--n-color-embedded);
  border-radius: 8px;
  font-size: 13px;
}

.breadcrumb-item {
  display: flex;
  align-items: center;
  gap: 4px;
  color: var(--n-text-color-2);
  cursor: pointer;
  padding: 2px 6px;
  border-radius: 4px;
  transition: all 0.2s ease;
}

.breadcrumb-item:hover {
  color: var(--n-primary-color);
  background: rgba(99, 102, 241, 0.1);
}

.breadcrumb-item.active {
  color: var(--n-text-color-1);
  font-weight: 500;
}

.breadcrumb-sep {
  color: var(--n-text-color-4);
}

/* 搜索框 */
.search-input {
  border-radius: 8px;
}

/* 目录列表 */
.folder-list {
  height: 280px;
  overflow-y: auto;
  border: 1px solid var(--n-border-color);
  border-radius: 10px;
  background: var(--n-color-embedded);
}

.folder-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 14px;
  cursor: pointer;
  transition: all 0.2s ease;
  border-bottom: 1px solid var(--n-border-color);
}

.folder-item:last-child {
  border-bottom: none;
}

.folder-item:hover {
  background: var(--n-color-hover);
}

.folder-item:hover .enter-icon {
  opacity: 1;
  transform: translateX(0);
}

.folder-item.back {
  background: rgba(99, 102, 241, 0.05);
}

.folder-icon {
  width: 32px;
  height: 32px;
  border-radius: 8px;
  background: rgba(245, 158, 11, 0.1);
  color: #f59e0b;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.folder-icon.cloud-icon {
  background: rgba(59, 130, 246, 0.1);
  color: #3b82f6;
}

.folder-icon.back-icon {
  background: rgba(99, 102, 241, 0.1);
  color: var(--n-primary-color);
}

.folder-name {
  flex: 1;
  font-size: 14px;
  color: var(--n-text-color-1);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.enter-icon {
  color: var(--n-primary-color);
  opacity: 0;
  transform: translateX(-8px);
  transition: all 0.2s ease;
}

/* 空状态 */
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  height: 200px;
  color: var(--n-text-color-3);
}

/* 当前路径 */
.current-path {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 14px;
  background: linear-gradient(135deg, rgba(99, 102, 241, 0.08) 0%, rgba(139, 92, 246, 0.08) 100%);
  border-radius: 8px;
  font-size: 13px;
}

.path-label {
  color: var(--n-text-color-3);
  flex-shrink: 0;
}

.path-value {
  color: var(--n-primary-color);
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 按钮样式 */
.browser-modal :deep(.n-button--primary-type) {
  box-shadow: 0 4px 12px rgba(99, 102, 241, 0.3);
}

.browser-modal :deep(.n-button--primary-type:hover) {
  box-shadow: 0 6px 16px rgba(99, 102, 241, 0.4);
}
</style>
