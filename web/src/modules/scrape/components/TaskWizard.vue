<script setup lang="ts">
import { ref } from 'vue'
import {
  NModal,
  NCard,
  NSteps,
  NStep,
  NButton,
  NSpace,
  NIcon,
} from 'naive-ui'
import {
  CloseOutline,
  ArrowBackOutline,
  ArrowForwardOutline,
  CheckmarkCircleOutline,
} from '@vicons/ionicons5'
import { useTaskWizard } from '@/modules/scrape/hooks/useTaskWizard'
import type { StorageLocator } from '@/shared/types/common'
import PathSelectStep from './wizard/PathSelectStep.vue'
import OptionsStep from './wizard/OptionsStep.vue'
import PreviewStep from './wizard/PreviewStep.vue'
import AdvancedSettingsModal from './AdvancedSettingsModal.vue'

const props = defineProps<{
  show: boolean
  initialScanPath?: string
  initialScanLocator?: StorageLocator | null
}>()

const emit = defineEmits<{
  (e: 'update:show', value: boolean): void
  (e: 'success'): void
}>()

// 高级设置弹窗（与 ManualJobCreateModal 同一组件/同一写入路径）
const showAdvancedSettings = ref(false)

// 向导状态机 + 预览 + 提交（逻辑见 useTaskWizard）
const {
  currentStep,
  submitting,
  previewLoading,
  previewError,
  loadPreviewFiles,
  previewFiles,
  previewTotal,
  advancedSettings,
  scanLocator,
  targetLocator,
  metadataLocator,
  allowLocalOutput,
  formData,
  steps,
  watchedFolders,
  globalOrganizeConfig,
  configReuseOptions,
  involvesP115,
  showMoveOptions,
  canProceed,
  handleClose,
  prevStep,
  nextStep,
  handleSubmit,
  handleConfigReuseChange,
  handleAdvancedSettingsConfirm,
} = useTaskWizard({
  show: () => props.show,
  initialScanPath: () => props.initialScanPath,
  initialScanLocator: () => props.initialScanLocator,
  onSuccess: () => emit('success'),
  onClose: () => emit('update:show', false),
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
      class="wizard-modal"
      :bordered="false"
      role="dialog"
      aria-modal="true"
    >
      <!-- 头部 -->
      <template #header>
        <div class="wizard-header">
          <span class="wizard-title">创建刮削任务</span>
          <NButton quaternary circle size="small" @click="handleClose" aria-label="关闭">
            <template #icon>
              <NIcon :component="CloseOutline" />
            </template>
          </NButton>
        </div>
      </template>

      <!-- 步骤指示器 -->
      <div class="wizard-steps">
        <NSteps :current="currentStep" size="small">
          <NStep
            v-for="(step, index) in steps"
            :key="index"
            :title="step.title"
          />
        </NSteps>
      </div>

      <!-- 步骤内容 -->
      <div class="wizard-content">
        <!-- 步骤 1: 选择路径 -->
        <PathSelectStep
          v-if="currentStep === 1"
          v-model:scan-path="formData.scan_path"
          v-model:target-folder="formData.target_folder"
          v-model:metadata-dir="formData.metadata_dir"
          v-model:scan-locator="scanLocator"
          v-model:target-locator="targetLocator"
          v-model:metadata-locator="metadataLocator"
          :watched-folders="watchedFolders"
          :global-config="globalOrganizeConfig"
        />

        <!-- 步骤 2: 配置选项 -->
        <OptionsStep
          v-else-if="currentStep === 2"
          v-model:link-mode="formData.link_mode"
          v-model:delete-empty-parent="formData.delete_empty_parent"
          v-model:allow-local-output="allowLocalOutput"
          :config-reuse-id="formData.config_reuse_id"
          :config-reuse-options="configReuseOptions"
          :involves-p115="involvesP115"
          :show-move-options="showMoveOptions"
          :advanced-settings="advancedSettings"
          @update:config-reuse-id="handleConfigReuseChange"
          @open-advanced="showAdvancedSettings = true"
        />

        <!-- 步骤 3: 预览确认 -->
        <PreviewStep
          v-else
          :form-data="formData"
          :advanced-settings="advancedSettings"
          :preview-files="previewFiles"
          :preview-total="previewTotal"
          :loading="previewLoading"
          :error="previewError"
          @retry="loadPreviewFiles"
        />
      </div>

      <!-- 底部操作 -->
      <template #footer>
        <div class="wizard-footer">
          <div class="footer-left">
            <span class="step-indicator">步骤 {{ currentStep }} / 3</span>
          </div>
          <NSpace>
            <NButton v-if="currentStep > 1" @click="prevStep">
              <template #icon>
                <NIcon :component="ArrowBackOutline" />
              </template>
              上一步
            </NButton>
            <NButton v-if="currentStep < 3" type="primary" :disabled="!canProceed" @click="nextStep">
              下一步
              <template #icon>
                <NIcon :component="ArrowForwardOutline" />
              </template>
            </NButton>
            <NButton
              v-else
              type="primary"
              :loading="submitting"
              @click="handleSubmit"
            >
              <template #icon>
                <NIcon :component="CheckmarkCircleOutline" />
              </template>
              开始刮削
            </NButton>
          </NSpace>
        </div>
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
.wizard-modal {
  width: 680px;
  max-width: 95vw;
  border-radius: 20px;
  background: var(--bg-surface);
  box-shadow: 0 25px 80px rgb(var(--black-rgb) / 20%);
}

.wizard-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.wizard-title {
  font-size: 20px;
  font-weight: 700;
  color: var(--text-1);
}

.wizard-steps {
  padding: 0 8px 24px;
  border-bottom: 1px solid var(--border-1);
  margin-bottom: 24px;
}

.wizard-steps :deep(.n-steps) {
  --n-indicator-size: 28px;
}

.wizard-steps :deep(.n-step-indicator) {
  border-radius: 50%;
}

.wizard-steps :deep(.n-step-content__title) {
  font-weight: 500;
}

.wizard-content {
  min-height: 320px;
  padding: 0 8px;
}

.wizard-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-top: 8px;
}

.footer-left {
  display: flex;
  align-items: center;
  gap: 12px;
}

.step-indicator {
  font-size: 13px;
  color: var(--text-3);
}

/* 按钮样式 */
.wizard-modal :deep(.n-button--primary-type) {
  background: var(--brand-500);
  box-shadow: 0 4px 14px rgb(var(--brand-rgb) / 35%);
  transition: all 0.25s ease;
}

.wizard-modal :deep(.n-button--primary-type:hover:not(:disabled)) {
  box-shadow: 0 6px 20px rgb(var(--brand-rgb) / 45%);
  transform: translateY(-1px);
}

.wizard-modal :deep(.n-button--primary-type:active) {
  transform: translateY(0);
}
</style>
