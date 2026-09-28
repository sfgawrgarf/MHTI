<script setup lang="ts">
import { ref, watch } from 'vue'
import { NButton, NCard, NIcon, NModal, NSpace, NTabs, NTabPane } from 'naive-ui'
import { CloseOutline } from '@vicons/ionicons5'
import type { AdvancedSettingsForm, ManualJobAdvancedSettings } from '@/modules/scrape/types'
import FolderBrowserModal from '@/shared/components/business/FolderBrowserModal.vue'
import OrganizeTab from '@/modules/scrape/components/advanced/OrganizeTab.vue'
import DownloadTab from '@/modules/scrape/components/advanced/DownloadTab.vue'
import NamingTab from '@/modules/scrape/components/advanced/NamingTab.vue'
import MetadataTab from '@/modules/scrape/components/advanced/MetadataTab.vue'

const props = defineProps<{
  show: boolean
}>()

const emit = defineEmits<{
  (e: 'update:show', value: boolean): void
  (e: 'confirm', settings: ManualJobAdvancedSettings): void
}>()

const activeTab = ref('organize')
const showMetadataFolderBrowser = ref(false)

// 默认文件类型白名单
const defaultExtWhitelist = ['mp4', 'avi', 'rmvb', 'wmv', 'mov', 'mkv', 'webm', 'iso', 'mpg', 'm4v', 'ts', 'flv', 'strm', 'vob', 'm2ts']

// 表单数据
const formData = ref<AdvancedSettingsForm>({
  metadata_folder: '',
  delete_metadata_on_fail: false,
  overwrite_video: false,
  overwrite_image: false,
  file_size_filter: 100,
  file_ext_whitelist: [...defaultExtWhitelist],
  file_name_blacklist: [],
  file_sanitize_list: [],
  protect_ext_whitelist: false,
  delete_by_size: false,
  delete_by_ext: false,
  delete_by_name: false,
  extra_ext_whitelist: [],
  download_poster: true,
  download_thumb: true,
  download_fanart: false,
  series_folder_template: '{title} ({year})',
  season_folder_template: 'Season {season}',
  episode_file_template: '{title} - S{season:02d}E{episode:02d}',
  scrape_title: true,
  scrape_plot: true,
  nfo_enabled: true,
})

// 各标签页的全局配置开关
const useGlobalOrganize = ref(true)
const useGlobalDownload = ref(true)
const useGlobalNaming = ref(true)
const useGlobalMetadata = ref(true)

// 新增标签输入
const newExtInput = ref('')
const newBlacklistInput = ref('')

// 关闭弹窗
const handleClose = () => {
  emit('update:show', false)
}

// 确认保存
const handleConfirm = () => {
  // 合并分类开关和表单数据
  emit('confirm', {
    ...formData.value,
    use_global_organize: useGlobalOrganize.value,
    use_global_download: useGlobalDownload.value,
    use_global_naming: useGlobalNaming.value,
    use_global_metadata: useGlobalMetadata.value,
  })
  handleClose()
}

// 处理元数据目录选择
const handleMetadataFolderConfirm = (path: string) => {
  formData.value.metadata_folder = path
}

// 监听显示状态重置标签页
watch(() => props.show, (show) => {
  if (show) {
    activeTab.value = 'organize'
  }
})
</script>

<template>
  <NModal
    :show="show"
    :mask-closable="false"
    transform-origin="center"
    @update:show="emit('update:show', $event)"
  >
    <NCard class="advanced-modal" :bordered="false">
      <template #header>
        <span class="header-title">高级设置</span>
      </template>
      <template #header-extra>
        <NButton quaternary circle size="small" @click="handleClose" aria-label="关闭">
          <template #icon>
            <NIcon :component="CloseOutline" />
          </template>
        </NButton>
      </template>

      <NTabs v-model:value="activeTab" type="line" animated>
        <!-- 整理标签页 -->
        <NTabPane name="organize" tab="整理">
          <OrganizeTab
            v-model:use-global="useGlobalOrganize"
            v-model:new-ext-input="newExtInput"
            v-model:new-blacklist-input="newBlacklistInput"
            :form-data="formData"
            @open-metadata-browser="showMetadataFolderBrowser = true"
          />
        </NTabPane>

        <!-- 下载标签页 -->
        <NTabPane name="download" tab="下载">
          <DownloadTab
            v-model:use-global="useGlobalDownload"
            :form-data="formData"
          />
        </NTabPane>

        <!-- 命名标签页 -->
        <NTabPane name="naming" tab="命名">
          <NamingTab
            v-model:use-global="useGlobalNaming"
            :form-data="formData"
          />
        </NTabPane>

        <!-- 元数据标签页 -->
        <NTabPane name="metadata" tab="元数据">
          <MetadataTab
            v-model:use-global="useGlobalMetadata"
            :form-data="formData"
          />
        </NTabPane>
      </NTabs>

      <template #footer>
        <NSpace justify="end">
          <NButton @click="handleClose">取消</NButton>
          <NButton type="primary" @click="handleConfirm">确认</NButton>
        </NSpace>
      </template>
    </NCard>
  </NModal>

  <!-- 元数据目录选择弹窗 -->
  <FolderBrowserModal
    v-model:show="showMetadataFolderBrowser"
    title="选择元数据目录"
    @confirm="handleMetadataFolderConfirm"
  />
</template>

<style scoped>
.advanced-modal {
  width: 720px;
  max-width: 95vw;
  border-radius: 16px;
  background: var(--bg-surface);
  box-shadow: 0 20px 60px rgb(var(--black-rgb) / 15%);
}

.header-title {
  font-size: 18px;
  font-weight: 600;
}

/* 按钮样式 */
.advanced-modal :deep(.n-button--primary-type) {
  box-shadow: 0 4px 12px rgb(var(--brand-rgb) / 30%);
}

.advanced-modal :deep(.n-button--primary-type:hover) {
  box-shadow: 0 6px 16px rgb(var(--brand-rgb) / 40%);
}

/* 标签页样式 */
.advanced-modal :deep(.n-tabs-tab) {
  font-weight: 500;
}
</style>
