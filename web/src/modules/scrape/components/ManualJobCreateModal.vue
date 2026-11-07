<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { NButton, NCard, NDivider, NForm, NIcon, NModal } from 'naive-ui'
import { CloseOutline } from '@vicons/ionicons5'
import { useManualJobForm } from '@/modules/scrape/hooks/useManualJobForm'
import { LINK_MODE_OPTIONS_BASIC } from '@/modules/scrape/constants'
import type { StorageLocator } from '@/shared/types/common'
import type { ScannedFile } from '@/modules/library/types'
import PathSettingSection from '@/modules/scrape/components/manual/PathSettingSection.vue'
import OrganizeSettingSection from '@/modules/scrape/components/manual/OrganizeSettingSection.vue'
import ManualJobFooter from '@/modules/scrape/components/manual/ManualJobFooter.vue'
import AdvancedSettingsModal from './AdvancedSettingsModal.vue'

const props = defineProps<{
  show: boolean
  initialScanPath?: string
  initialScanLocator?: StorageLocator | null
  selectedFiles?: ScannedFile[]
}>()

const emit = defineEmits<{
  'update:show': [value: boolean]
  success: []
}>()

const showAdvancedSettings = ref(false)

// 表单（含配置加载、复用回填、校验、提交）
const {
  submitting,
  advancedSettings,
  formData,
  scanLocator,
  targetLocator,
  metadataLocator,
  allowLocalOutput,
  configReuseOptions,
  involvesP115,
  showMoveOptions,
  loadWatchedFolders,
  loadGlobalConfig,
  handleConfigReuseChange,
  handleClose,
  handleSubmit,
  handleAdvancedSettingsConfirm,
} = useManualJobForm({
  show: () => props.show,
  initialScanPath: () => props.initialScanPath,
  initialScanLocator: () => props.initialScanLocator,
  selectedFiles: () => props.selectedFiles,
  onSuccess: () => emit('success'),
  onClose: () => emit('update:show', false),
})

// 整理模式选项（见 constants.ts）
const linkModeOptions = LINK_MODE_OPTIONS_BASIC

onMounted(() => {
  loadWatchedFolders()
  loadGlobalConfig()
})
</script>

<template>
  <NModal
    :show="show"
    :mask-closable="false"
    transform-origin="center"
    @update:show="emit('update:show', $event)"
  >
    <NCard
      class="create-modal"
      :bordered="false"
      role="dialog"
      aria-modal="true"
    >
      <!-- 头部 -->
      <template #header>
        <span class="header-title">创建手动任务</span>
      </template>
      <template #header-extra>
        <NButton quaternary circle size="small" @click="handleClose" aria-label="关闭">
          <template #icon>
            <NIcon :component="CloseOutline" />
          </template>
        </NButton>
      </template>

      <!-- 内容 -->
      <div class="modal-body">
        <p class="modal-desc">{{ selectedFiles?.length ? `仅处理已选的 ${selectedFiles.length} 个文件` : '手动任务会在后台根据创建顺序依次执行' }}</p>
        <ul v-if="selectedFiles?.length"><li v-for="file in selectedFiles" :key="file.path">{{ file.path }}</li></ul>

        <NForm :model="formData" label-placement="top" class="create-form">
          <!-- 路径设置 -->
          <PathSettingSection
            :lock-scan-path="!!selectedFiles?.length"
            v-model:scan-path="formData.scan_path"
            v-model:target-folder="formData.target_folder"
            v-model:metadata-dir="formData.metadata_dir"
            v-model:scan-locator="scanLocator"
            v-model:target-locator="targetLocator"
            v-model:metadata-locator="metadataLocator"
          />

          <NDivider style="margin: 16px 0" />

          <!-- 整理设置 -->
          <OrganizeSettingSection
            v-model:link-mode="formData.link_mode"
            v-model:delete-empty-parent="formData.delete_empty_parent"
            v-model:allow-local-output="allowLocalOutput"
            :config-reuse-id="formData.config_reuse_id"
            :link-mode-options="linkModeOptions"
            :show-move-options="showMoveOptions"
            :config-reuse-options="configReuseOptions"
            :involves-p115="involvesP115"
            @update:config-reuse-id="handleConfigReuseChange"
          />
        </NForm>
      </div>

      <!-- 底部 -->
      <template #footer>
        <ManualJobFooter
          :has-advanced-settings="!!advancedSettings"
          :submitting="submitting"
          @open-advanced="showAdvancedSettings = true"
          @close="handleClose"
          @submit="handleSubmit"
        />
      </template>
    </NCard>
  </NModal>

  <!-- 高级设置弹窗 -->
  <AdvancedSettingsModal
    v-model:show="showAdvancedSettings"
    @confirm="handleAdvancedSettingsConfirm"
  />
</template>

<style scoped>
.create-modal {
  width: 600px;
  max-width: 95vw;
  border-radius: 16px;
  background: var(--bg-surface);
  box-shadow: 0 20px 60px rgb(var(--black-rgb) / 15%);
}

.header-title {
  font-size: 18px;
  font-weight: 600;
}

.modal-body {
  padding: 0 4px;
}

.modal-desc {
  color: var(--text-3);
  font-size: 13px;
  margin: 0 0 20px 0;
  padding: 10px 14px;
  background: var(--bg-subtle);
  border-radius: 8px;
}

/* 表单样式优化 */
.create-form :deep(.n-form-item) {
  margin-bottom: 16px;
}

.create-form :deep(.n-form-item-label) {
  font-weight: 500;
}

.create-form :deep(.n-input),
.create-form :deep(.n-select) {
  border-radius: 8px;
}

/* 按钮样式 */
.create-modal :deep(.n-button--primary-type) {
  box-shadow: 0 4px 12px rgb(var(--brand-rgb) / 30%);
}

.create-modal :deep(.n-button--primary-type:hover) {
  box-shadow: 0 6px 16px rgb(var(--brand-rgb) / 40%);
}
</style>
