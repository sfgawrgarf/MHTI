<script setup lang="ts">
/**
 * 高级设置 - 整理标签页
 *
 * 状态全在父组件：formData 与两个标签输入框经 props 传入（输入框值
 * 留在父层是因为 NTabPane 默认 displayDirective:'if'，切 tab 会卸载
 * 内容——放本组件内会导致「输入一半切走再切回内容丢失」的可见变更）。
 */
import { reactive } from 'vue'
import { NButton, NForm, NFormItem, NIcon, NInput, NInputNumber, NSpace, NTag } from 'naive-ui'
import { FolderOutline } from '@vicons/ionicons5'
import type { AdvancedSettingsForm } from '@/modules/scrape/types'
import GlobalSwitchRow from '@/modules/scrape/components/advanced/GlobalSwitchRow.vue'

const props = defineProps<{
  formData: AdvancedSettingsForm
  useGlobal: boolean
  newExtInput: string
  newBlacklistInput: string
}>()

const formData = reactive(props.formData)

const emit = defineEmits<{
  'update:useGlobal': [value: boolean]
  'update:newExtInput': [value: string]
  'update:newBlacklistInput': [value: string]
  'open-metadata-browser': []
}>()

// 添加标签
const addTag = (list: string[], value: string) => {
  const trimmed = value.trim()
  if (trimmed && !list.includes(trimmed)) {
    list.push(trimmed)
  }
}

// 删除标签
const removeTag = (list: string[], index: number) => {
  list.splice(index, 1)
}
</script>

<template>
  <GlobalSwitchRow
    :model-value="useGlobal"
    @update:model-value="emit('update:useGlobal', $event)"
  />
  <template v-if="!useGlobal">
    <NForm :model="formData" label-placement="left" label-width="auto" class="settings-form">
      <NFormItem label="元数据目录">
        <div class="path-input">
          <NInput
            v-model:value="formData.metadata_folder"
            placeholder="留空则跟随视频目录"
          />
          <NButton @click="emit('open-metadata-browser')" aria-label="选择元数据目录">
            <template #icon>
              <NIcon :component="FolderOutline" />
            </template>
          </NButton>
        </div>
      </NFormItem>
      <NFormItem label="文件大小过滤">
        <NSpace align="center">
          <NInputNumber v-model:value="formData.file_size_filter" :min="0" style="width: 120px" />
          <span class="form-hint">MB，小于此大小的文件将被忽略</span>
        </NSpace>
      </NFormItem>
      <NFormItem label="文件类型白名单">
        <div class="tag-input-area">
          <NTag v-for="(ext, index) in formData.file_ext_whitelist" :key="ext" closable size="small" @close="removeTag(formData.file_ext_whitelist, index)">{{ ext }}</NTag>
          <NInput
            :value="newExtInput"
            placeholder="添加"
            size="small"
            class="tag-add-input"
            @update:value="emit('update:newExtInput', $event)"
            @keyup.enter="addTag(formData.file_ext_whitelist, newExtInput); emit('update:newExtInput', '')"
          />
        </div>
      </NFormItem>
      <NFormItem label="文件名黑名单">
        <div class="tag-input-area">
          <NTag v-for="(name, index) in formData.file_name_blacklist" :key="name" closable size="small" @close="removeTag(formData.file_name_blacklist, index)">{{ name }}</NTag>
          <NInput
            :value="newBlacklistInput"
            placeholder="添加"
            size="small"
            class="tag-add-input"
            @update:value="emit('update:newBlacklistInput', $event)"
            @keyup.enter="addTag(formData.file_name_blacklist, newBlacklistInput); emit('update:newBlacklistInput', '')"
          />
        </div>
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
  color: var(--text-3);
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
</style>
