import { ref, computed, watch, onMounted } from 'vue'
import { FolderOutline, SettingsOutline, CheckmarkCircleOutline } from '@vicons/ionicons5'
import { fsApi } from '@/shared/api/fs'
import type { DirectoryEntry, StorageLocator } from '@/shared/types/common'
import { useManualJobForm } from '@/modules/scrape/hooks/useManualJobForm'

/** 预览可识别的视频扩展名（原 TaskWizard 内联清单） */
const VIDEO_EXTS = ['mp4', 'mkv', 'avi', 'mov', 'wmv', 'flv', 'webm', 'ts', 'rmvb', 'm4v']

/**
 * 创建任务向导（TaskWizard）
 *
 * 表单数据 / 三个 locator / 配置复用 / 高级设置 / 提交 payload 全部委托
 * useManualJobForm —— 与 ManualJobCreateModal **完全同源**（默认 link_mode、
 * 字段集、成功提示时机一致），本 hook 只叠加三步状态机与文件预览。
 *
 * 原实现的两个缺陷已随同源化消除：
 * 1. advanced_settings 恒为 null（步骤 2 无入口写入，现由高级设置弹窗写入）；
 * 2. 提交成功时向导与页面各弹一次提示（现只由页面弹一次）。
 */
export function useTaskWizard(options: {
  show: () => boolean
  initialScanPath: () => string | undefined
  initialScanLocator: () => StorageLocator | null | undefined
  onSuccess: () => void
  onClose: () => void
}) {
  const {
    submitting,
    advancedSettings,
    formData,
    scanLocator,
    targetLocator,
    metadataLocator,
    allowLocalOutput,
    watchedFolders,
    globalOrganizeConfig,
    configReuseOptions,
    involvesP115,
    showMoveOptions,
    loadWatchedFolders,
    loadGlobalConfig,
    handleConfigReuseChange,
    handleClose: closeForm,
    handleSubmit: submitForm,
    handleAdvancedSettingsConfirm,
  } = useManualJobForm({ ...options, onClose: () => { resetWizardState(); options.onClose() } })

  // 向导状态
  const currentStep = ref(1)
  const previewLoading = ref(false)
  const previewFiles = ref<DirectoryEntry[]>([])
  const previewTotal = ref(0)
  let previewGeneration = 0

  // 步骤配置
  const steps = [
    { title: '选择路径', icon: FolderOutline },
    { title: '配置选项', icon: SettingsOutline },
    { title: '预览确认', icon: CheckmarkCircleOutline },
  ]

  // 是否可以进入下一步
  const canProceed = computed(() => {
    if (currentStep.value === 1) {
      return formData.value.scan_path.trim() !== '' && formData.value.target_folder.trim() !== ''
    }
    return true
  })

  // 加载预览文件
  const loadPreviewFiles = async () => {
    if (!formData.value.scan_path) return

    previewLoading.value = true
    const generation = ++previewGeneration
    try {
      const provider = scanLocator.value?.provider
      const fileId = scanLocator.value?.file_id
      const response = await fsApi.browse(formData.value.scan_path, 1, 10, provider, fileId)
      if (generation !== previewGeneration || !options.show()) return
      // 过滤出视频文件
      previewFiles.value = response.entries.filter((entry) => {
        if (entry.is_dir) return true
        const ext = entry.name.split('.').pop()?.toLowerCase() || ''
        return VIDEO_EXTS.includes(ext)
      })
      previewTotal.value = response.total
    } catch (error) {
      if (generation !== previewGeneration) return
      console.error('加载预览失败:', error)
      previewFiles.value = []
      previewTotal.value = 0
    } finally {
      if (generation === previewGeneration) previewLoading.value = false
    }
  }

  /** 向导自身状态复位（表单与 locator 由 useManualJobForm 复位） */
  const resetWizardState = () => {
    previewGeneration++
    previewLoading.value = false
    currentStep.value = 1
    previewFiles.value = []
    previewTotal.value = 0
  }

  // 关闭弹窗
  const handleClose = () => {
    closeForm()
    resetWizardState()
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

  // 监听弹窗打开，刷新监控目录与全局配置
  watch(options.show, async (newVal) => {
    resetWizardState()
    if (newVal) {
      await Promise.all([loadWatchedFolders(), loadGlobalConfig()])
    }
  })

  onMounted(() => {
    loadWatchedFolders()
    loadGlobalConfig()
  })

  return {
    // 向导状态
    currentStep,
    previewLoading,
    previewFiles,
    previewTotal,
    steps,
    canProceed,
    loadPreviewFiles,
    handleClose,
    prevStep,
    nextStep,
    // 表单与提交（与 ManualJobCreateModal 同源）
    submitting,
    advancedSettings,
    formData,
    scanLocator,
    targetLocator,
    metadataLocator,
    allowLocalOutput,
    watchedFolders,
    globalOrganizeConfig,
    configReuseOptions,
    involvesP115,
    showMoveOptions,
    handleConfigReuseChange,
    handleSubmit: submitForm,
    handleAdvancedSettingsConfirm,
  }
}
