<script setup lang="ts">
/**
 * 高级设置 - 下载标签页
 *
 * 状态在父组件：formData 与开关均经 props 传入。
 */
import { reactive } from 'vue'
import { NForm, NFormItem, NSwitch } from 'naive-ui'
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
</template>

<style scoped>
/* 表单样式 */
.settings-form {
  padding: 8px 0;
}

.settings-form :deep(.n-form-item) {
  margin-bottom: 16px;
}
</style>
