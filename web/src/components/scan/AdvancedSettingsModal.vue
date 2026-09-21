<script setup lang="ts">
import { ref, watch } from 'vue'
import {
  NModal,
  NCard,
  NTabs,
  NTabPane,
  NForm,
  NFormItem,
  NInput,
  NInputNumber,
  NSwitch,
  NButton,
  NSpace,
  NIcon,
  NTag,
} from 'naive-ui'
import { CloseOutline } from '@vicons/ionicons5'
import type { ManualJobAdvancedSettings } from '@/api/types'
import { LinkMode } from '@/api/types'

const props = defineProps<{
  show: boolean
  linkMode: LinkMode
  targetFolder: string
}>()

const emit = defineEmits<{
  (e: 'update:show', value: boolean): void
  (e: 'confirm', settings: ManualJobAdvancedSettings): void
}>()

const activeTab = ref('organize')
// 表单数据
const formData = ref<Omit<ManualJobAdvancedSettings, 'use_global_organize' | 'use_global_download' | 'use_global_naming' | 'use_global_metadata'>>({
  scan_filters_enabled: true,
  metadata_folder: '',
  delete_metadata_on_fail: false,
  overwrite_video: false,
  overwrite_image: false,
  file_size_filter: 100,
  file_ext_whitelist: [],
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
  process_subtitle: true,
})

// 各标签页的全局配置开关
const useGlobalOrganize = ref(true)
const useGlobalDownload = ref(true)
const useGlobalNaming = ref(true)
const useGlobalMetadata = ref(true)

// 新增标签输入
const newExtInput = ref('')
const newBlacklistInput = ref('')

// 添加标签
const addTag = (list: string[], input: { value: string }) => {
  const value = input.value.trim()
  if (value && !list.includes(value)) {
    list.push(value)
    input.value = ''
  }
}

// 删除标签
const removeTag = (list: string[], index: number) => {
  list.splice(index, 1)
}

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
        <NButton quaternary circle size="small" @click="handleClose">
          <template #icon>
            <NIcon :component="CloseOutline" />
          </template>
        </NButton>
      </template>

      <NTabs v-model:value="activeTab" type="line" animated>
        <!-- 整理标签页 -->
        <NTabPane name="organize" tab="整理">
          <div class="global-switch">
            <span>使用全局配置</span>
            <NSwitch v-model:value="useGlobalOrganize" />
          </div>
          <template v-if="!useGlobalOrganize">
            <NForm :model="formData" label-placement="left" label-width="auto" class="settings-form">
              <NFormItem label="文件大小过滤">
                <NSpace align="center">
                  <NInputNumber v-model:value="formData.file_size_filter" :min="0" style="width: 120px" />
                  <span class="form-hint">MB，小于此大小的文件将被忽略</span>
                </NSpace>
              </NFormItem>
              <NFormItem label="文件类型白名单">
                <div class="tag-input-area">
                  <NTag v-for="(ext, index) in formData.file_ext_whitelist" :key="ext" closable size="small" @close="removeTag(formData.file_ext_whitelist, index)">{{ ext }}</NTag>
                  <NInput v-model:value="newExtInput" placeholder="添加" size="small" class="tag-add-input" @keyup.enter="addTag(formData.file_ext_whitelist, { value: newExtInput }); newExtInput = ''" />
                </div>
                <template #feedback>留空表示使用系统支持的全部视频格式</template>
              </NFormItem>
              <NFormItem label="文件名黑名单">
                <div class="tag-input-area">
                  <NTag v-for="(name, index) in formData.file_name_blacklist" :key="name" closable size="small" @close="removeTag(formData.file_name_blacklist, index)">{{ name }}</NTag>
                  <NInput v-model:value="newBlacklistInput" placeholder="添加" size="small" class="tag-add-input" @keyup.enter="addTag(formData.file_name_blacklist, { value: newBlacklistInput }); newBlacklistInput = ''" />
                </div>
              </NFormItem>
            </NForm>
          </template>
        </NTabPane>

        <!-- 下载标签页 -->
        <NTabPane name="download" tab="下载">
          <div class="global-switch">
            <span>使用全局配置</span>
            <NSwitch v-model:value="useGlobalDownload" />
          </div>
          <template v-if="!useGlobalDownload">
            <NForm :model="formData" label-placement="left" label-width="auto" class="settings-form">
              <NFormItem label="下载海报">
                <NSwitch v-model:value="formData.download_poster" />
              </NFormItem>
              <NFormItem label="下载缩略图">
                <NSwitch v-model:value="formData.download_thumb" />
              </NFormItem>
              <NFormItem label="下载同人图">
                <NSwitch v-model:value="formData.download_fanart" />
              </NFormItem>
            </NForm>
          </template>
        </NTabPane>

        <!-- 命名标签页 -->
        <NTabPane name="naming" tab="命名">
          <div class="global-switch">
            <span>使用全局配置</span>
            <NSwitch v-model:value="useGlobalNaming" />
          </div>
          <template v-if="!useGlobalNaming">
            <NForm :model="formData" label-placement="top" class="settings-form">
              <NFormItem label="剧集文件夹模板">
                <NInput v-model:value="formData.series_folder_template" placeholder="{title} ({year})" />
              </NFormItem>
              <NFormItem label="季文件夹模板">
                <NInput v-model:value="formData.season_folder_template" placeholder="Season {season}" />
              </NFormItem>
              <NFormItem label="剧集文件模板">
                <NInput v-model:value="formData.episode_file_template" placeholder="{title} - S{season:02d}E{episode:02d}" />
              </NFormItem>
              <div class="form-hint">可用变量: {title}, {year}, {season}, {episode}, {episode_title}</div>
            </NForm>
          </template>
        </NTabPane>

        <!-- 元数据标签页 -->
        <NTabPane name="metadata" tab="元数据">
          <div class="global-switch">
            <span>使用全局配置</span>
            <NSwitch v-model:value="useGlobalMetadata" />
          </div>
          <template v-if="!useGlobalMetadata">
            <NForm :model="formData" label-placement="left" label-width="auto" class="settings-form">
              <NFormItem label="生成NFO文件">
                <NSwitch v-model:value="formData.nfo_enabled" />
              </NFormItem>
              <NFormItem label="处理字幕文件">
                <NSwitch v-model:value="formData.process_subtitle" />
              </NFormItem>
            </NForm>
          </template>
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

</template>

<style scoped>
.advanced-modal {
  width: 720px;
  max-width: 95vw;
  border-radius: 16px;
  background: var(--ios-glass-bg-thick);
  backdrop-filter: blur(20px);
  -webkit-backdrop-filter: blur(20px);
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.15);
}

.header-title {
  font-size: 18px;
  font-weight: 600;
}

/* 全局配置开关 */
.global-switch {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  background: linear-gradient(135deg, rgba(99, 102, 241, 0.08) 0%, rgba(139, 92, 246, 0.08) 100%);
  border-radius: 10px;
  margin-bottom: 16px;
  font-size: 14px;
  font-weight: 500;
  color: var(--n-text-color-1);
}

/* 表单样式 */
.settings-form {
  padding: 8px 0;
}

.settings-form :deep(.n-form-item) {
  margin-bottom: 16px;
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
  color: var(--n-text-color-3);
}

/* 标签输入区域 */
.tag-input-area {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
}

.tag-add-input {
  width: 80px;
}

/* 按钮样式 */
.advanced-modal :deep(.n-button--primary-type) {
  box-shadow: 0 4px 12px rgba(99, 102, 241, 0.3);
}

.advanced-modal :deep(.n-button--primary-type:hover) {
  box-shadow: 0 6px 16px rgba(99, 102, 241, 0.4);
}

/* 标签页样式 */
.advanced-modal :deep(.n-tabs-tab) {
  font-weight: 500;
}
</style>
