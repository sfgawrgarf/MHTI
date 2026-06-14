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
  NEmpty,
  NInputGroup,
  NTag,
  useMessage,
} from 'naive-ui'
import {
  FolderOutline,
  ArrowUpOutline,
  RefreshOutline,
  CloseOutline,
  CloudOutline,
} from '@vicons/ionicons5'
import { filesApi } from '@/api/files'
import type { DirectoryEntry, StorageLocator, StorageProvider } from '@/api/types'

const props = withDefaults(defineProps<{
  modelValue?: string
  show?: boolean
  title?: string
}>(), {
  modelValue: '',
  show: false,
  title: '选择文件夹',
})

const emit = defineEmits<{
  'update:modelValue': [value: string]
  'update:show': [value: boolean]
  select: [path: string]
  selectLocator: [locator: StorageLocator]
}>()

const message = useMessage()
const loading = ref(false)
const currentPath = ref('')
const parentPath = ref<string | null>(null)
const entries = ref<DirectoryEntry[]>([])
const inputPath = ref('')
// 存储提供方跟踪：本地浏览 vs 115 网盘
const currentProvider = ref<StorageProvider>('local')
const currentFileId = ref<string | null>(null)
// 父目录的 file_id（来自后端响应），返回上级时直接使用，无需前端维护栈
const parentFileId = ref<string | null>(null)

const VIRTUAL_115_ROOT = '/115网盘'

// 双向绑定
const selectedPath = computed({
  get: () => props.modelValue,
  set: (value) => emit('update:modelValue', value),
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
    inputPath.value = response.current_path
    currentProvider.value = effectiveProvider
    // 优先用后端返回的 file_id（115 子目录必须），fallback 到请求时的值
    currentFileId.value = response.current_file_id ?? effectiveFileId ?? null
    parentFileId.value = response.parent_file_id ?? null
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
  // 点击虚拟 115 根入口 → 切到 115 provider
  if (entry.is_virtual && entry.provider === '115') {
    loadDirectory(entry.path, '115', entry.file_id ?? '0')
    return
  }
  // 同 provider 内进入子目录：115 用 file_id，本地用 path
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

// 刷新
const refresh = () => {
  loadDirectory(currentPath.value)
}

// 通过输入路径跳转（仅本地支持手动输入跳转）
const goToPath = () => {
  if (currentProvider.value === '115') {
    message.info('115 网盘请通过目录列表导航')
    return
  }
  if (inputPath.value) {
    loadDirectory(inputPath.value, 'local', null)
  }
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

// 选择当前目录
const selectCurrentPath = () => {
  selectedPath.value = currentPath.value
  emit('select', currentPath.value)
  emit('selectLocator', buildLocator())
  emit('update:show', false)
}

// 关闭弹窗
const handleClose = () => {
  emit('update:show', false)
}

// 只显示目录
const directories = computed(() => {
  return entries.value.filter((e) => e.is_dir)
})

// 当前 provider 标签
const providerTag = computed(() => {
  if (currentProvider.value === '115') return { label: '115 网盘', type: 'info' as const }
  return { label: '本地', type: 'default' as const }
})

// 监听 show 变化，打开时加载目录
watch(
  () => props.show,
  (newShow) => {
    if (newShow) {
      // 重置为本地根开始浏览
      currentProvider.value = 'local'
      currentFileId.value = null
      parentFileId.value = null
      loadDirectory(props.modelValue || '', 'local', null)
    }
  },
  { immediate: true }
)
</script>

<template>
  <NModal
    :show="show"
    :mask-closable="true"
    @update:show="emit('update:show', $event)"
  >
    <NCard
      :title="title"
      size="small"
      class="folder-browser-modal"
      :bordered="false"
      closable
      @close="handleClose"
    >
      <template #header-extra>
        <NSpace align="center" size="small">
          <NTag size="small" :type="providerTag.type">{{ providerTag.label }}</NTag>
          <NButton quaternary circle size="small" :loading="loading" @click="refresh">
            <template #icon>
              <NIcon :component="RefreshOutline" />
            </template>
          </NButton>
        </NSpace>
      </template>

      <NSpace vertical>
        <!-- 路径输入（仅本地） -->
        <NInputGroup v-if="currentProvider === 'local'">
          <NInput
            v-model:value="inputPath"
            placeholder="输入路径"
            @keyup.enter="goToPath"
          />
          <NButton type="primary" @click="goToPath">跳转</NButton>
        </NInputGroup>
        <NInput v-else :value="currentPath" readonly placeholder="115 网盘路径" />

        <!-- 当前选择 -->
        <NSpace v-if="selectedPath">
          <span>已选择: {{ selectedPath }}</span>
        </NSpace>

        <!-- 目录列表 -->
        <NSpin :show="loading">
          <div class="folder-list">
            <!-- 返回上级 -->
            <div
              v-if="parentPath !== null || currentProvider === '115'"
              class="folder-item"
              @click="goUp"
            >
              <NIcon :component="ArrowUpOutline" size="20" />
              <span class="folder-name">..</span>
            </div>

            <!-- 空目录 -->
            <NEmpty v-if="directories.length === 0 && !loading" description="目录为空" />

            <!-- 目录项 -->
            <div
              v-for="entry in directories"
              :key="entry.path"
              class="folder-item"
              @click="enterDirectory(entry)"
            >
              <NIcon
                :component="entry.is_virtual ? CloudOutline : FolderOutline"
                size="20"
              />
              <span class="folder-name">{{ entry.name }}</span>
            </div>
          </div>
        </NSpin>

        <!-- 选择按钮 -->
        <NButton
          type="primary"
          block
          :disabled="!currentPath"
          @click="selectCurrentPath"
        >
          选择此文件夹
        </NButton>
      </NSpace>
    </NCard>
  </NModal>
</template>

<style scoped>
.folder-browser-modal {
  width: 500px;
  max-width: 90vw;
  border-radius: 12px;
}

.folder-list {
  max-height: 300px;
  overflow-y: auto;
  border: 1px solid var(--n-border-color);
  border-radius: 8px;
}

.folder-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 14px;
  cursor: pointer;
  transition: background-color 0.2s;
}

.folder-item:hover {
  background-color: var(--n-color-hover);
}

.folder-item:active {
  background-color: var(--n-color-pressed);
}

.folder-name {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 响应式 */
@media (max-width: 640px) {
  .folder-browser-modal {
    width: 100%;
    max-width: 100%;
    margin: 0;
    border-radius: 0;
    height: 100vh;
    max-height: 100vh;
  }

  .folder-list {
    max-height: calc(100vh - 280px);
  }
}
</style>
