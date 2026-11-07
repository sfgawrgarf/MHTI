<script setup lang="ts">
/**
 * 步骤 2：整理设置
 *
 * 与 ManualJobCreateModal 同源：整理模式 / 空目录删除 / 配置复用 / 本地输出
 * 直接复用 OrganizeSettingSection，保证两条创建流程的字段与默认值一致。
 *
 * 原 7 个开关（下载海报/背景图/缩略图/NFO/字幕/删除空父目录/覆盖已存在）
 * 均不在提交 payload 中（ManualJobCreate 只接受 advanced_settings），
 * 属装饰性开关，已删除；其真实语义改由「高级设置」入口
 * （AdvancedSettingsModal → advanced_settings）承载。
 */
import { NButton, NIcon } from 'naive-ui'
import { SettingsOutline } from '@vicons/ionicons5'
import type { SelectMixedOption } from 'naive-ui/es/select/src/interface'
import { LINK_MODE_OPTIONS_BASIC } from '@/modules/scrape/constants'
import type { LinkMode, ManualJobAdvancedSettings } from '@/modules/scrape/types'
import OrganizeSettingSection from '@/modules/scrape/components/manual/OrganizeSettingSection.vue'

defineProps<{
  linkMode: LinkMode
  deleteEmptyParent: boolean
  configReuseId: string | null
  configReuseOptions: SelectMixedOption[]
  involvesP115: boolean
  allowLocalOutput: boolean
  showMoveOptions: boolean
  advancedSettings: ManualJobAdvancedSettings | null
}>()

const emit = defineEmits<{
  'update:linkMode': [value: LinkMode]
  'update:deleteEmptyParent': [value: boolean]
  'update:configReuseId': [value: string]
  'update:allowLocalOutput': [value: boolean]
  /** 打开高级设置弹窗（父组件持有弹窗实例） */
  openAdvanced: []
}>()

// 整理模式选项（见 constants.ts，与 ManualJobCreateModal 同一份）
const linkModeOptions = LINK_MODE_OPTIONS_BASIC
</script>

<template>
  <div class="options-step">
    <OrganizeSettingSection
      :link-mode="linkMode"
      :link-mode-options="linkModeOptions"
      :delete-empty-parent="deleteEmptyParent"
      :show-move-options="showMoveOptions"
      :config-reuse-id="configReuseId"
      :config-reuse-options="configReuseOptions"
      :involves-p115="involvesP115"
      :allow-local-output="allowLocalOutput"
      @update:link-mode="emit('update:linkMode', $event)"
      @update:delete-empty-parent="emit('update:deleteEmptyParent', $event)"
      @update:config-reuse-id="emit('update:configReuseId', $event)"
      @update:allow-local-output="emit('update:allowLocalOutput', $event)"
    />

    <div class="advanced-row">
      <NButton quaternary @click="emit('openAdvanced')">
        <template #icon>
          <NIcon :component="SettingsOutline" />
        </template>
        高级设置
        <span v-if="advancedSettings" class="advanced-dot"></span>
      </NButton>
      <span class="advanced-hint">整理 / 下载 / 命名 / 元数据分类，未设置时沿用全局配置</span>
    </div>
  </div>
</template>

<style scoped>
.options-step {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.advanced-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding-top: 4px;
}

.advanced-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--brand-500);
  margin-left: 6px;
}

.advanced-hint {
  font-size: 12px;
  color: var(--text-3);
}
</style>
