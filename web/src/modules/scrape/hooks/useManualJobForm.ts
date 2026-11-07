import { ref, computed, watch } from 'vue'
import { useMessage } from 'naive-ui'
import { manualJobApi } from '@/modules/scrape/api'
import { LinkMode } from '@/modules/scrape/types'
import type { ManualJobAdvancedSettings } from '@/modules/scrape/types'
import type { StorageLocator } from '@/shared/types/common'
import type { ScannedFile } from '@/modules/library/types'
import { ORGANIZE_MODE_TO_LINK_MODE } from '@/modules/scrape/constants'
import { useWatcherConfig } from '@/modules/scrape/hooks/useWatcherConfig'

/**
 * 创建任务表单（ManualJobCreateModal，同时被 TaskWizard 复用为表单内核）
 *
 * 表单数据 / 三个 locator / 配置复用回填 watch / 重置 / 校验 / 提交。
 * 配置加载复用 useWatcherConfig。默认 link_mode 与后端 ManualJobCreate 一致（MOVE）。
 */
export function useManualJobForm(options: {
  /** 弹窗显示状态（用于打开时回填初始路径） */
  show: () => boolean
  /** 初始扫描路径 */
  initialScanPath: () => string | undefined
  /** 初始扫描 locator */
  initialScanLocator: () => StorageLocator | null | undefined
  selectedFiles?: () => ScannedFile[] | undefined
  /** 提交成功回调（原 emit('success')） */
  onSuccess: () => void
  /** 关闭回调（原 emit('update:show', false)） */
  onClose: () => void
}) {
  const message = useMessage()
  const { watchedFolders, globalOrganizeConfig, configReuseOptions, loadWatchedFolders, loadGlobalConfig } =
    useWatcherConfig()

  const submitting = ref(false)

  const advancedSettings = ref<ManualJobAdvancedSettings | null>(null)

  interface ManualJobFormData {
    scan_path: string
    target_folder: string
    metadata_dir: string
    link_mode: LinkMode
    delete_empty_parent: boolean
    config_reuse_id: string | null
  }

  const formData = ref<ManualJobFormData>({
    scan_path: '',
    target_folder: '',
    metadata_dir: '',
    link_mode: LinkMode.MOVE,
    delete_empty_parent: true,
    config_reuse_id: null,
  })

  const scanLocator = ref<StorageLocator | null>(null)
  const targetLocator = ref<StorageLocator | null>(null)
  const metadataLocator = ref<StorageLocator | null>(null)
  const allowLocalOutput = ref(false)

  // 是否涉及 115（本地输出开关的显示条件）—— 原实现只判断 scan/target
  const involvesP115 = computed(
    () =>
      scanLocator.value?.provider === '115' ||
      targetLocator.value?.provider === '115',
  )

  // 是否显示移动模式选项
  const showMoveOptions = computed(() => formData.value.link_mode === LinkMode.MOVE)

  const handleConfigReuseChange = (value: string) => {
    formData.value.config_reuse_id = value || null
  }

  // 重置表单
  const resetForm = () => {
    formData.value = {
      scan_path: '',
      target_folder: '',
      metadata_dir: '',
      link_mode: LinkMode.MOVE,
      delete_empty_parent: true,
      config_reuse_id: null,
    }
    advancedSettings.value = null
    scanLocator.value = null
    targetLocator.value = null
    metadataLocator.value = null
    allowLocalOutput.value = false
  }

  // 关闭弹窗
  const handleClose = () => {
    options.onClose()
    resetForm()
  }

  // 提交表单
  const handleSubmit = async () => {
    if (submitting.value) return
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
        selected_files: options.selectedFiles?.(),
      })
      options.onSuccess()
      handleClose()
    } catch (error) {
      message.error('创建任务失败')
      console.error(error)
    } finally {
      submitting.value = false
    }
  }

  // 配置复用变化时自动填充全局配置
  watch(() => formData.value.config_reuse_id, (newVal) => {
    if (newVal !== null && globalOrganizeConfig.value) {
      const folder = watchedFolders.value.find((f) => f.id === newVal)
      if (folder && !options.selectedFiles?.()?.length) {
        // 使用监控目录路径作为扫描路径
        formData.value.scan_path = folder.path
        scanLocator.value = folder.provider === '115' ? {
          provider: '115', path: folder.path, file_id: folder.file_id, is_dir: true,
        } : null
      }
      // 使用全局配置填充整理目录和元数据目录
      const config = globalOrganizeConfig.value
      if (config.organize_dir) {
        formData.value.target_folder = config.organize_dir
        targetLocator.value = null
      }
      if (config.metadata_dir) {
        formData.value.metadata_dir = config.metadata_dir
        metadataLocator.value = null
      }
      // 设置整理模式
      formData.value.link_mode = ORGANIZE_MODE_TO_LINK_MODE[config.organize_mode] || LinkMode.MOVE
      formData.value.delete_empty_parent = config.auto_clean_source
    }
  })

  // 监听弹窗打开，设置初始扫描路径与 locator
  watch(options.show, (newVal) => {
    if (newVal) {
      if (options.initialScanPath()) {
        formData.value.scan_path = options.initialScanPath()!
      }
      if (options.initialScanLocator()) {
        scanLocator.value = options.initialScanLocator()!
      }
    }
  })

  // 高级设置确认
  const handleAdvancedSettingsConfirm = (settings: ManualJobAdvancedSettings) => {
    advancedSettings.value = settings
  }

  return {
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
    resetForm,
    handleClose,
    handleSubmit,
    handleAdvancedSettingsConfirm,
  }
}
