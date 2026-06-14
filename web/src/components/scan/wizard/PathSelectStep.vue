<script setup lang="ts">
import { computed } from 'vue'
import {
  NFormItem,
  NInput,
  NInputGroup,
  NButton,
  NIcon,
  NSelect,
  NTooltip,
  NAlert,
  NSwitch,
} from 'naive-ui'
import {
  FolderOpenOutline,
  InformationCircleOutline,
} from '@vicons/ionicons5'
import type { WatchedFolder, OrganizeConfig, StorageLocator } from '@/api/types'
import FolderBrowser from '../FolderBrowser.vue'
import { ref } from 'vue'

const props = defineProps<{
  scanPath: string
  targetFolder: string
  metadataDir: string
  scanLocator: StorageLocator | null
  targetLocator: StorageLocator | null
  watchedFolders: WatchedFolder[]
  globalConfig: OrganizeConfig | null
  allowLocalOutput: boolean
}>()

const emit = defineEmits<{
  (e: 'update:scanPath', value: string): void
  (e: 'update:targetFolder', value: string): void
  (e: 'update:metadataDir', value: string): void
  (e: 'update:scanLocator', value: StorageLocator | null): void
  (e: 'update:targetLocator', value: StorageLocator | null): void
  (e: 'update:metadataLocator', value: StorageLocator | null): void
  (e: 'update:allowLocalOutput', value: boolean): void
}>()

// 是否涉及 115（显示本地输出开关）
const involvesP115 = computed(
  () =>
    props.scanLocator?.provider === '115' ||
    props.targetLocator?.provider === '115',
)

// 文件夹浏览器状态
const showBrowser = ref(false)
const browserMode = ref<'scan' | 'target' | 'metadata'>('scan')

// 监控目录选项
const watchedFolderOptions = computed(() => {
  return props.watchedFolders.map(folder => ({
    label: folder.path,
    value: folder.path,
    targetFolder: folder.output_dir,
  }))
})

// 打开文件夹浏览器
const openBrowser = (mode: 'scan' | 'target' | 'metadata') => {
  browserMode.value = mode
  showBrowser.value = true
}

// 选择文件夹
const handleFolderSelect = (path: string) => {
  if (browserMode.value === 'scan') {
    emit('update:scanPath', path)
  } else if (browserMode.value === 'target') {
    emit('update:targetFolder', path)
  } else {
    emit('update:metadataDir', path)
  }
  showBrowser.value = false
}

// 接收文件夹选择器的 locator（115 等云端目录）
const handleFolderLocator = (locator: StorageLocator) => {
  if (browserMode.value === 'scan') {
    emit('update:scanLocator', locator)
  } else if (browserMode.value === 'target') {
    emit('update:targetLocator', locator)
  } else {
    emit('update:metadataLocator', locator)
  }
}

// 从监控目录快速填充
const handleWatchedFolderSelect = (path: string) => {
  emit('update:scanPath', path)
  const folder = props.watchedFolders.find(f => f.path === path)
  if (folder?.output_dir) {
    emit('update:targetFolder', folder.output_dir)
  }
}

// 使用全局配置的整理目录
const useGlobalTargetFolder = () => {
  if (props.globalConfig?.target_folder) {
    emit('update:targetFolder', props.globalConfig.target_folder)
  }
}
</script>

<template>
  <div class="path-select-step">
    <NAlert type="info" :bordered="false" class="step-tip">
      <template #icon>
        <NIcon :component="InformationCircleOutline" />
      </template>
      选择要刮削的文件夹和整理后的目标位置
    </NAlert>

    <!-- 刮削路径 -->
    <NFormItem label="刮削路径" required>
      <div class="path-input-group">
        <NInputGroup>
          <NInput
            :value="scanPath"
            placeholder="输入或选择要刮削的文件夹路径"
            @update:value="emit('update:scanPath', $event)"
          />
          <NButton @click="openBrowser('scan')">
            <template #icon>
              <NIcon :component="FolderOpenOutline" />
            </template>
          </NButton>
        </NInputGroup>
        <NSelect
          v-if="watchedFolderOptions.length > 0"
          :value="null"
          :options="watchedFolderOptions"
          placeholder="从监控目录选择"
          clearable
          style="margin-top: 8px"
          @update:value="handleWatchedFolderSelect"
        />
      </div>
    </NFormItem>

    <!-- 整理目录 -->
    <NFormItem label="整理目录" required>
      <div class="path-input-group">
        <NInputGroup>
          <NInput
            :value="targetFolder"
            placeholder="输入或选择整理后的目标文件夹"
            @update:value="emit('update:targetFolder', $event)"
          />
          <NButton @click="openBrowser('target')">
            <template #icon>
              <NIcon :component="FolderOpenOutline" />
            </template>
          </NButton>
        </NInputGroup>
        <div v-if="globalConfig?.target_folder" class="quick-fill">
          <NButton text size="small" type="primary" @click="useGlobalTargetFolder">
            使用全局配置: {{ globalConfig.target_folder }}
          </NButton>
        </div>
      </div>
    </NFormItem>

    <!-- 元数据目录（可选） -->
    <NFormItem label="元数据目录">
      <template #label>
        <div class="label-with-tip">
          <span>元数据目录</span>
          <NTooltip>
            <template #trigger>
              <NIcon :component="InformationCircleOutline" class="tip-icon" />
            </template>
            可选。如不填写，元数据将保存在视频文件同目录
          </NTooltip>
        </div>
      </template>
      <NInputGroup>
        <NInput
          :value="metadataDir"
          placeholder="可选，留空则与视频同目录"
          @update:value="emit('update:metadataDir', $event)"
        />
        <NButton @click="openBrowser('metadata')">
          <template #icon>
            <NIcon :component="FolderOpenOutline" />
          </template>
        </NButton>
      </NInputGroup>
    </NFormItem>

    <!-- 本地输出开关（仅 115 场景） -->
    <NFormItem v-if="involvesP115" label="本地输出">
      <div style="display: flex; align-items: center; gap: 12px;">
        <NSwitch
          :value="allowLocalOutput"
          @update:value="emit('update:allowLocalOutput', $event)"
        />
        <span style="font-size: 13px; color: var(--n-text-color-2);">允许下载 115 文件到本地输出</span>
      </div>
      <template #feedback>
        <span style="font-size: 12px; color: var(--n-text-color-3);">115 源文件默认在线处理；开启后下载到本地整理</span>
      </template>
    </NFormItem>

    <!-- 文件夹浏览器 -->
    <FolderBrowser
      v-model:show="showBrowser"
      :title="browserMode === 'scan' ? '选择刮削路径' : browserMode === 'target' ? '选择整理目录' : '选择元数据目录'"
      @select="handleFolderSelect"
      @select-locator="handleFolderLocator"
    />
  </div>
</template>

<style scoped>
.path-select-step {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.step-tip {
  background: var(--ios-bg-tertiary);
  border-radius: 12px;
}

.step-tip :deep(.n-alert-body) {
  padding: 12px 16px;
}

.path-input-group {
  width: 100%;
}

.quick-fill {
  margin-top: 8px;
}

.label-with-tip {
  display: flex;
  align-items: center;
  gap: 6px;
}

.tip-icon {
  font-size: 14px;
  color: var(--ios-text-tertiary);
  cursor: help;
}

:deep(.n-form-item-label) {
  font-weight: 500;
}

:deep(.n-input) {
  border-radius: 10px;
}

:deep(.n-input-group .n-input) {
  border-top-right-radius: 0;
  border-bottom-right-radius: 0;
}

:deep(.n-input-group .n-button) {
  border-top-left-radius: 0;
  border-bottom-left-radius: 0;
}
</style>
