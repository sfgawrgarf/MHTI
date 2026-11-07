<script setup lang="ts">
/**
 * 手动任务 - 整理设置区（整理模式 / 空目录删除 / 配置复用 / 本地输出）
 *
 * 纯展示：表单字段与开关由父组件持有，经 v-model 透传。
 * 配置复用下拉的 value 兜底 `?? 0` 与选项来源保持原实现。
 */
import { NFormItem, NIcon, NSelect, NSwitch } from 'naive-ui'
import { SettingsOutline } from '@vicons/ionicons5'
import type { LinkMode } from '@/modules/scrape/types'
import type { SelectMixedOption } from 'naive-ui/es/select/src/interface'

defineProps<{
  linkMode: LinkMode
  linkModeOptions: SelectMixedOption[]
  deleteEmptyParent: boolean
  showMoveOptions: boolean
  configReuseId: string | null
  configReuseOptions: SelectMixedOption[]
  involvesP115: boolean
  allowLocalOutput: boolean
}>()

const emit = defineEmits<{
  'update:linkMode': [value: LinkMode]
  'update:deleteEmptyParent': [value: boolean]
  'update:configReuseId': [value: string]
  'update:allowLocalOutput': [value: boolean]
}>()
</script>

<template>
  <div class="form-section">
    <div class="section-title">
      <NIcon :component="SettingsOutline" :size="16" />
      <span>整理设置</span>
    </div>

    <NFormItem label="整理模式">
      <NSelect
        :value="linkMode"
        :options="linkModeOptions"
        @update:value="emit('update:linkMode', $event)"
      />
    </NFormItem>

    <NFormItem v-if="showMoveOptions" label="空目录自动删除">
      <div class="switch-row">
        <NSwitch
          :value="deleteEmptyParent"
          @update:value="emit('update:deleteEmptyParent', $event)"
        />
        <span class="switch-label">移动后自动删除空目录</span>
      </div>
    </NFormItem>

    <NFormItem label="配置复用">
      <NSelect
        :value="configReuseId ?? ''"
        :options="configReuseOptions"
        placeholder="从监控目录复制配置"
        @update:value="emit('update:configReuseId', $event)"
      />
    </NFormItem>

    <NFormItem v-if="involvesP115" label="本地输出">
      <div class="switch-row">
        <NSwitch
          :value="allowLocalOutput"
          @update:value="emit('update:allowLocalOutput', $event)"
        />
        <span class="switch-label">允许下载 115 文件到本地输出</span>
      </div>
      <template #feedback>
        <span class="form-hint">115 源文件默认在线处理；开启后下载到本地整理</span>
      </template>
    </NFormItem>
  </div>
</template>

<style scoped>
.form-section {
  margin-bottom: 8px;
}

.section-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 14px;
  font-weight: 600;
  color: var(--text-1);
  margin-bottom: 12px;
}

.section-title .n-icon {
  color: var(--brand-500);
}

.switch-row {
  display: flex;
  align-items: center;
  gap: 12px;
}

.switch-label {
  font-size: 13px;
  color: var(--text-2);
}

.form-hint {
  font-size: 12px;
  color: var(--text-3);
}
</style>