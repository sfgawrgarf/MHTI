<script setup lang="ts">
import { ref, computed, watch, onMounted } from 'vue'
import {
  NModal,
  NCard,
  NSteps,
  NStep,
  NButton,
  NSpace,
  NIcon,
  useMessage,
} from 'naive-ui'
import {
  CloseOutline,
  FolderOutline,
  SettingsOutline,
  CheckmarkCircleOutline,
  ArrowBackOutline,
  ArrowForwardOutline,
} from '@vicons/ionicons5'
import { manualJobApi } from '@/api/manual-job'
import { watcherApi } from '@/api/watcher'
import { configApi } from '@/api/config'
import { filesApi } from '@/api/files'
import { LinkMode } from '@/api/types'
import type { WatchedFolder, ManualJobAdvancedSettings, OrganizeConfig, DirectoryEntry, StorageLocator } from '@/api/types'
import PathSelectStep from './wizard/PathSelectStep.vue'
import OptionsStep from './wizard/OptionsStep.vue'
import PreviewStep from './wizard/PreviewStep.vue'

const props = defineProps<{
  show: boolean
  initialScanPath?: string
}>()

const emit = defineEmits<{
  (e: 'update:show', value: boolean): void
  (e: 'success'): void
}>()

const message = useMessage()

// 向导状态
const currentStep = ref(1)
const submitting = ref(false)
const previewLoading = ref(false)

// 预览数据
const previewFiles = ref<DirectoryEntry[]>([])
const previewTotal = ref(0)

// 配置数据
const watchedFolders = ref<WatchedFolder[]>([])
const globalOrganizeConfig = ref<OrganizeConfig | null>(null)

// 高级设置
const advancedSettings = ref<ManualJobAdvancedSettings | null>(null)

// 存储定位信息（115 等云端目录）
const scanLocator = ref<StorageLocator | null>(null)
const targetLocator = ref<StorageLocator | null>(null)
const metadataLocator = ref<StorageLocator | null>(null)
const allowLocalOutput = ref(false)

// 表单数据
const formData = ref({
  scan_path: '',
  target_folder: '',
  metadata_dir: '',
  link_mode: LinkMode.HARDLINK as LinkMode,
  delete_empty_parent: true,
  config_reuse_id: null as number | null,
  // 下载选项
  download_poster: true,
  download_backdrop: true,
  download_thumbnail: true,
  generate_nfo: true,
  process_subtitle: true,
  overwrite_existing: false,
})

// 步骤配置
const steps = [
  { title: '选择路径', icon: FolderOutline },
  { title: '配置选项', icon: SettingsOutline },
  { title: '预览确认', icon: CheckmarkCircleOutline },
]

// 当前步骤状态
const currentStepStatus = computed(() => {
  if (currentStep.value === 1) {
    return formData.value.scan_path && formData.value.target_folder ? 'finish' : 'process'
  }
  return 'process'
})

// 是否可以进入下一步
const canProceed = computed(() => {
  if (currentStep.value === 1) {
    return formData.value.scan_path.trim() !== '' && formData.value.target_folder.trim() !== ''
  }
  return true
})

// 加载监控目录
const loadWatchedFolders = async () => {
  try {
    const response = await watcherApi.listFolders()
    watchedFolders.value = response.folders
  } catch (error) {
    console.error('加载监控目录失败:', error)
  }
}

// 加载全局配置
const loadGlobalConfig = async () => {
  try {
    globalOrganizeConfig.value = await configApi.getOrganizeConfig()
  } catch (error) {
    console.error('加载全局配置失败:', error)
  }
}

// 加载预览文件
const loadPreviewFiles = async () => {
  if (!formData.value.scan_path) return

  previewLoading.value = true
  try {
    const provider = scanLocator.value?.provider
    const fileId = scanLocator.value?.file_id
    const response = await filesApi.browse(formData.value.scan_path, 1, 10, provider, fileId)
    // 过滤出视频文件
    previewFiles.value = response.entries.filter(entry => {
      if (entry.is_dir) return true
      const ext = entry.name.split('.').pop()?.toLowerCase() || ''
      return ['mp4', 'mkv', 'avi', 'mov', 'wmv', 'flv', 'webm', 'ts', 'rmvb', 'm4v'].includes(ext)
    })
    previewTotal.value = response.total
  } catch (error) {
    console.error('加载预览失败:', error)
    previewFiles.value = []
    previewTotal.value = 0
  } finally {
    previewLoading.value = false
  }
}

// 重置表单
const resetForm = () => {
  currentStep.value = 1
  formData.value = {
    scan_path: '',
    target_folder: '',
    metadata_dir: '',
    link_mode: LinkMode.HARDLINK,
    delete_empty_parent: true,
    config_reuse_id: null,
    download_poster: true,
    download_backdrop: true,
    download_thumbnail: true,
    generate_nfo: true,
    process_subtitle: true,
    overwrite_existing: false,
  }
  advancedSettings.value = null
  previewFiles.value = []
  previewTotal.value = 0
  scanLocator.value = null
  targetLocator.value = null
  metadataLocator.value = null
  allowLocalOutput.value = false
}

// 关闭弹窗
const handleClose = () => {
  emit('update:show', false)
  resetForm()
}

// 上一步
const prevStep = () => {
  if (currentStep.value > 1) {
    currentStep.value--
  }
}

// 下一步
const nextStep = async () => {
  if (currentStep.value < 3) {
    currentStep.value++
    // 进入预览步骤时加载预览
    if (currentStep.value === 3) {
      await loadPreviewFiles()
    }
  }
}

// 提交表单
const handleSubmit = async () => {
  if (!formData.value.scan_path.trim()) {
    message.warning('请输入刮削路径')
    return
  }
  if (!formData.value.target_folder.trim()) {
    message.warning('请输入整理目录')
    return
  }

  submitting.value = true
  try {
    await manualJobApi.create({
      scan_path: formData.value.scan_path.trim(),
      target_folder: formData.value.target_folder.trim(),
      metadata_dir: formData.value.metadata_dir.trim(),
      scan_locator: scanLocator.value,
      target_locator: targetLocator.value,
      metadata_locator: metadataLocator.value,
      allow_local_output: allowLocalOutput.value,
      link_mode: formData.value.link_mode,
      delete_empty_parent: formData.value.delete_empty_parent,
      config_reuse_id: formData.value.config_reuse_id,
      advanced_settings: advancedSettings.value,
    })
    message.success('任务创建成功')
    emit('success')
    handleClose()
  } catch (error) {
    message.error('创建任务失败')
    console.error(error)
  } finally {
    submitting.value = false
  }
}

// 更新高级设置
const updateAdvancedSettings = (settings: ManualJobAdvancedSettings) => {
  advancedSettings.value = settings
}

// 监听弹窗打开
watch(() => props.show, async (newVal) => {
  if (newVal) {
    if (props.initialScanPath) {
      formData.value.scan_path = props.initialScanPath
    }
    await Promise.all([loadWatchedFolders(), loadGlobalConfig()])
  }
})

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
      class="wizard-modal"
      :bordered="false"
      role="dialog"
      aria-modal="true"
    >
      <!-- 头部 -->
      <template #header>
        <div class="wizard-header">
          <span class="wizard-title">创建刮削任务</span>
          <NButton quaternary circle size="small" @click="handleClose">
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
          :allow-local-output="allowLocalOutput"
          @update:allow-local-output="allowLocalOutput = $event"
          :watched-folders="watchedFolders"
          :global-config="globalOrganizeConfig"
        />

        <!-- 步骤 2: 配置选项 -->
        <OptionsStep
          v-else-if="currentStep === 2"
          v-model:link-mode="formData.link_mode"
          v-model:delete-empty-parent="formData.delete_empty_parent"
          v-model:download-poster="formData.download_poster"
          v-model:download-backdrop="formData.download_backdrop"
          v-model:download-thumbnail="formData.download_thumbnail"
          v-model:generate-nfo="formData.generate_nfo"
          v-model:process-subtitle="formData.process_subtitle"
          v-model:overwrite-existing="formData.overwrite_existing"
          :advanced-settings="advancedSettings"
          @update:advanced-settings="updateAdvancedSettings"
        />

        <!-- 步骤 3: 预览确认 -->
        <PreviewStep
          v-else
          :form-data="formData"
          :preview-files="previewFiles"
          :preview-total="previewTotal"
          :loading="previewLoading"
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
</template>

<style scoped>
.wizard-modal {
  width: 680px;
  max-width: 95vw;
  border-radius: 20px;
  background: var(--ios-glass-bg-thick);
  backdrop-filter: blur(20px);
  -webkit-backdrop-filter: blur(20px);
  box-shadow: 0 25px 80px rgba(0, 0, 0, 0.2);
}

.wizard-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.wizard-title {
  font-size: 20px;
  font-weight: 700;
  color: var(--ios-text-primary);
}

.wizard-steps {
  padding: 0 8px 24px;
  border-bottom: 1px solid var(--ios-separator);
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
  color: var(--ios-text-tertiary);
}

/* 按钮样式 */
.wizard-modal :deep(.n-button--primary-type) {
  background: var(--ios-blue);
  box-shadow: 0 4px 14px rgba(0, 122, 255, 0.35);
  transition: all 0.25s ease;
}

.wizard-modal :deep(.n-button--primary-type:hover:not(:disabled)) {
  box-shadow: 0 6px 20px rgba(0, 122, 255, 0.45);
  transform: translateY(-1px);
}

.wizard-modal :deep(.n-button--primary-type:active) {
  transform: translateY(0);
}
</style>
