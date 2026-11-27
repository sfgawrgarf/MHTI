<script setup lang="ts">
/**
 * 步骤 3：预览确认
 *
 * 配置摘要 + 文件预览 + 覆盖风险提示。
 * 覆盖提示的数据源改为 advanced_settings（原绑定步骤 2 的装饰性开关，
 * 该开关已删除；只有「不使用全局整理配置且开启覆盖」时才提示）。
 */
import { computed } from 'vue'
import { NIcon } from 'naive-ui'
import { CloseCircleOutline } from '@vicons/ionicons5'
import type { LinkMode, ManualJobAdvancedSettings } from '@/modules/scrape/types'
import type { DirectoryEntry } from '@/shared/types/common'
import PreviewSummaryCard from '@/modules/scrape/components/wizard/PreviewSummaryCard.vue'
import PreviewFileList from '@/modules/scrape/components/wizard/PreviewFileList.vue'

const emit = defineEmits<{ retry: [] }>()
const props = defineProps<{
  formData: {
    scan_path: string
    target_folder: string
    metadata_dir: string
    link_mode: LinkMode
    delete_empty_parent: boolean
  }
  advancedSettings: ManualJobAdvancedSettings | null
  previewFiles: DirectoryEntry[]
  previewTotal: number
  error: string
  loading: boolean
}>()

// 覆盖已存在文件（仅当高级设置接管整理配置时才生效）
const overwriteEnabled = computed(() => {
  const settings = props.advancedSettings
  if (!settings || settings.use_global_organize) return false
  return settings.overwrite_video || settings.overwrite_image
})
</script>

<template>
  <div class="preview-step">
    <!-- 配置摘要 -->
    <PreviewSummaryCard
      :form-data="props.formData"
      :advanced-settings="props.advancedSettings"
    />

    <!-- 文件预览 -->
    <PreviewFileList
      :preview-files="props.previewFiles"
      :preview-total="props.previewTotal"
      :loading="props.loading"
      :error="props.error"
      @retry="emit('retry')"
    />

    <!-- 警告提示 -->
    <div v-if="overwriteEnabled" class="warning-box">
      <NIcon :component="CloseCircleOutline" class="warning-icon" />
      <span>已开启覆盖模式，现有文件可能被覆盖</span>
    </div>
  </div>
</template>

<style scoped>
.preview-step {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.warning-box {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 16px;
  background: rgb(var(--danger-rgb) / 10%);
  border: 1px solid var(--danger-500);
  border-radius: 12px;
  color: var(--danger-500);
  font-size: 13px;
}

.warning-icon {
  font-size: 18px;
}
</style>
