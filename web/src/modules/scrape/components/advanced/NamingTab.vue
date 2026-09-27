<script setup lang="ts">
/**
 * 高级设置 - 命名标签页
 *
 * 状态在父组件：formData 与开关均经 props 传入。
 * 注意本页 NForm 用 label-placement="top"（与整理/下载/元数据页的 left 不同）。
 */
import { reactive } from 'vue'
import { NForm, NFormItem, NInput } from 'naive-ui'
import type { AdvancedSettingsForm } from '@/modules/scrape/types'
import GlobalSwitchRow from '@/modules/scrape/components/advanced/GlobalSwitchRow.vue'

const props = defineProps<{
  formData: AdvancedSettingsForm
  useGlobal: boolean
}>()

const formData = reactive(props.formData)

const emit = defineEmits<{
  'update:useGlobal': [value: boolean]
}>()
</script>

<template>
  <GlobalSwitchRow
    :model-value="useGlobal"
    @update:model-value="emit('update:useGlobal', $event)"
  />
  <template v-if="!useGlobal">
    <NForm :model="formData" label-placement="top" class="settings-form">
      <NFormItem label="剧集文件夹模板">
        <NInput v-model:value="formData.series_folder_template" placeholder="{series_name} ({year})" />
      </NFormItem>
      <NFormItem label="季文件夹模板">
        <NInput v-model:value="formData.season_folder_template" placeholder="Season {season}" />
      </NFormItem>
      <NFormItem label="剧集文件模板">
        <NInput v-model:value="formData.episode_file_template" placeholder="{series_name} - S{season:02d}E{episode:02d}" />
      </NFormItem>
      <div class="form-hint">可用变量: {series_name}, {year}, {season}, {episode}, {episode_title}</div>
    </NForm>
  </template>
</template>

<style scoped>
/* 表单样式 */
.settings-form {
  padding: 8px 0;
}

.settings-form :deep(.n-form-item) {
  margin-bottom: 16px;
}

.form-hint {
  font-size: 12px;
  color: var(--text-3);
}
</style>
