<script setup lang="ts">
/**
 * 冲突处理选项组（file_conflict 与 emby_conflict 步骤1 共用）
 *
 * 两处 DOM 结构一致，但选中机制不同：
 * - variant="group"（file_conflict）：NRadioGroup + v-model
 * - variant="manual"（emby_conflict）：每个选项 :checked + @click.stop，
 *   由外层 div 的 @click 触发选择（原实现如此）
 */
import { NIcon, NRadio, NRadioGroup } from 'naive-ui'
import { ArrowBackOutline } from '@vicons/ionicons5'

export interface ConflictOption {
  value: string
  label: string
  desc: string
  /** 是否显示右侧箭头（emby 的"更改季/集"项） */
  arrow?: boolean
}

withDefaults(defineProps<{
  modelValue: string
  options: ConflictOption[]
  variant?: 'group' | 'manual'
}>(), {
  variant: 'group',
})

const emit = defineEmits<{
  'update:modelValue': [value: string]
  /** manual 变体的"更改季/集"类项（arrow=true）触发 */
  action: [value: string]
}>()

const handleClick = (opt: ConflictOption) => {
  if (opt.arrow) {
    emit('action', opt.value)
  } else {
    emit('update:modelValue', opt.value)
  }
}
</script>

<template>
  <!-- group 变体：NRadioGroup 包裹 -->
  <NRadioGroup
    v-if="variant === 'group'"
    :value="modelValue"
    class="radio-group"
    @update:value="emit('update:modelValue', $event)"
  >
    <div
      v-for="opt in options"
      :key="opt.value"
      class="radio-option"
      :class="{ active: modelValue === opt.value }"
      @click="emit('update:modelValue', opt.value)"
    >
      <NRadio :value="opt.value" />
      <div class="option-content">
        <span class="option-label">{{ opt.label }}</span>
        <span class="option-desc">{{ opt.desc }}</span>
      </div>
    </div>
  </NRadioGroup>

  <!-- manual 变体：每项 checked + @click.stop，由外层 div 承担点击 -->
  <div v-else class="radio-group">
    <div
      v-for="opt in options"
      :key="opt.value"
      class="radio-option clickable"
      :class="{ active: !opt.arrow && modelValue === opt.value }"
      @click="handleClick(opt)"
    >
      <NRadio :checked="!opt.arrow && modelValue === opt.value" :value="opt.value" @click.stop />
      <div class="option-content">
        <span class="option-label">{{ opt.label }}</span>
        <span class="option-desc">{{ opt.desc }}</span>
      </div>
      <div v-if="opt.arrow" class="arrow-hint">
        <NIcon :component="ArrowBackOutline" :size="16" style="transform: rotate(180deg)" />
      </div>
    </div>
  </div>
</template>

<style scoped>
.radio-group {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.radio-option {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 14px 16px;
  border-radius: 10px;
  background: var(--bg-subtle);
  cursor: pointer;
  transition: all 0.2s ease;
}

.radio-option.active {
  background: rgb(var(--brand-rgb) / 12%);
  box-shadow: inset 0 0 0 2px var(--brand-500);
}

.radio-option:hover {
  background: var(--bg-subtle);
}

.option-content {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.option-label {
  font-size: 14px;
  font-weight: 500;
  color: var(--text-1);
}

.option-desc {
  font-size: 12px;
  color: var(--text-3);
}

/* arrow-hint：复刻原组件定义（opacity 0 隐藏；emby 的 radio-option 无
   hover 显示规则，故该箭头原本始终不可见——保持零视觉变更） */
.arrow-hint {
  display: flex;
  align-items: center;
  color: var(--text-3);
  opacity: 0;
  transition: opacity 0.2s ease;
}
</style>
