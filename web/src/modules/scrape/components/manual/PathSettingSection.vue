<script setup lang="ts">
/**
 * 手动任务 - 路径设置区（刮削路径 / 整理目录 / 元数据目录）
 *
 * 3 处目录选择弹窗收敛为单实例 + mode（与 PathSelectStep 同模式）：
 * 三个开关合并为 showBrowser + browserMode，标题随 mode 取。
 * 状态（路径与 locator）仍由父组件持有，经 v-model 透传。
 */
import { computed, ref } from 'vue'
import { NButton, NFormItem, NIcon, NInput } from 'naive-ui'
import { FolderOutline } from '@vicons/ionicons5'
import type { StorageLocator } from '@/shared/types/common'
import FolderBrowserModal from '@/shared/components/business/FolderBrowserModal.vue'

const props = defineProps<{
  scanPath: string
  lockScanPath?: boolean
  targetFolder: string
  metadataDir: string
  scanLocator: StorageLocator | null
  targetLocator: StorageLocator | null
  metadataLocator: StorageLocator | null
}>()

const emit = defineEmits<{
  'update:scanPath': [value: string]
  'update:targetFolder': [value: string]
  'update:metadataDir': [value: string]
  'update:scanLocator': [value: StorageLocator | null]
  'update:targetLocator': [value: StorageLocator | null]
  'update:metadataLocator': [value: StorageLocator | null]
}>()

type BrowserMode = 'scan' | 'target' | 'metadata'

const showBrowser = ref(false)
const browserMode = ref<BrowserMode>('scan')

// 三个入口的文案（与原三处 FolderBrowserModal 的 title 逐字一致）
const BROWSER_TITLES: Record<BrowserMode, string> = {
  scan: '选择刮削路径',
  target: '选择整理目录',
  metadata: '选择元数据目录',
}
const browserTitle = computed(() => BROWSER_TITLES[browserMode.value])

const openBrowser = (mode: BrowserMode) => {
  browserMode.value = mode
  showBrowser.value = true
}

// 选择文件夹（按 mode 分派到对应字段）
const handleConfirm = (path: string) => {
  if (browserMode.value === 'scan') {
    emit('update:scanPath', path)
  } else if (browserMode.value === 'target') {
    emit('update:targetFolder', path)
  } else {
    emit('update:metadataDir', path)
  }
  showBrowser.value = false
}

const handleConfirmLocator = (locator: StorageLocator) => {
  if (browserMode.value === 'scan') {
    emit('update:scanLocator', locator)
  } else if (browserMode.value === 'target') {
    emit('update:targetLocator', locator)
  } else {
    emit('update:metadataLocator', locator)
  }
}
</script>

<template>
  <div class="form-section">
    <div class="section-title">
      <NIcon :component="FolderOutline" :size="16" />
      <span>路径设置</span>
    </div>

    <NFormItem label="刮削路径" required>
      <div class="path-input">
        <NInput
          :value="props.scanPath"
          :disabled="lockScanPath"
          placeholder="请输入视频目录或文件路径"
          @update:value="emit('update:scanPath', $event)"
        />
        <NButton :disabled="lockScanPath" @click="openBrowser('scan')" aria-label="选择刮削路径">
          <template #icon>
            <NIcon :component="FolderOutline" />
          </template>
        </NButton>
      </div>
      <template #feedback>
        <span class="form-hint">{{ lockScanPath ? '仅处理上方列出的所选文件' : '指定目录时会扫描目录内全部视频文件' }}</span>
      </template>
    </NFormItem>

    <NFormItem label="整理目录" required>
      <div class="path-input">
        <NInput
          :value="props.targetFolder"
          placeholder="请输入整理结果存放目录"
          @update:value="emit('update:targetFolder', $event)"
        />
        <NButton @click="openBrowser('target')" aria-label="选择整理目录">
          <template #icon>
            <NIcon :component="FolderOutline" />
          </template>
        </NButton>
      </div>
    </NFormItem>

    <NFormItem label="元数据目录">
      <div class="path-input">
        <NInput
          :value="props.metadataDir"
          placeholder="请输入元数据存放目录（可选）"
          @update:value="emit('update:metadataDir', $event)"
        />
        <NButton @click="openBrowser('metadata')" aria-label="选择元数据目录">
          <template #icon>
            <NIcon :component="FolderOutline" />
          </template>
        </NButton>
      </div>
      <template #feedback>
        <span class="form-hint">NFO 和图片文件存放目录，留空则与视频同目录</span>
      </template>
    </NFormItem>

    <!-- 目录选择弹窗（单实例 + mode） -->
    <FolderBrowserModal
      v-model:show="showBrowser"
      :title="browserTitle"
      @confirm="handleConfirm"
      @confirm-locator="handleConfirmLocator"
    />
  </div>
</template>

<style scoped>
.form-section {
  margin-bottom: 8px;
}

.section-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 14px;
  font-weight: 600;
  color: var(--text-1);
  margin-bottom: 12px;
}

.section-title .n-icon {
  color: var(--brand-500);
}

.path-input {
  display: flex;
  gap: 8px;
  width: 100%;
}

.path-input .n-input {
  flex: 1;
}

.form-hint {
  font-size: 12px;
  color: var(--text-3);
}
</style>
