<script setup lang="ts">
/**
 * 设置字段行（settings 域私有版式件）
 *
 * 统一「标签列 + 控件 + 说明」：桌面端标签定宽 132px 左对齐，移动端自动改为标签置顶
 * （useMobileLayout 单例决定，切视口需刷新页面）。
 *
 * 控件宽度也收在这里：原先 11 个面板里散着 `style="width: 200px"` 之类的内联宽度，
 * 同一个「端口」字段在不同面板宽度都不一样。这里只暴露 4 档，想全宽就 full。
 */
import { computed } from 'vue'
import { NFormItem } from 'naive-ui'
import { useResponsiveValue } from '@/shared/composables/useMobileLayout'

withDefaults(
  defineProps<{
    label: string
    /** 字段说明：常驻在控件下方，不塞进 placeholder */
    hint?: string
    /** 控件宽度档位 */
    width?: 'sm' | 'md' | 'lg' | 'full'
  }>(),
  { width: 'md' },
)

/** 桌面标签在左、移动标签在顶：窄屏下左标签会把控件挤成一条 */
const placement = useResponsiveValue<'left' | 'top'>({ mobile: 'top', desktop: 'left' })
const labelWidth = computed(() => (placement.value === 'left' ? 132 : undefined))
</script>

<template>
  <NFormItem
    class="settings-field"
    :label="label"
    :label-placement="placement"
    :label-width="labelWidth"
    :show-feedback="false"
  >
    <div class="field-main" :class="`is-${width}`">
      <div class="field-control">
        <slot />
      </div>
      <p v-if="hint" class="field-hint">{{ hint }}</p>
    </div>
  </NFormItem>
</template>

<style scoped>
.field-main {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  min-width: 0;
}

/* 4 档控件宽度：以「设置页内容宽 720px - 标签列 132px - 间距」为上限推导 */
.field-main.is-sm {
  width: 140px;
}

.field-main.is-md {
  width: 220px;
}

.field-main.is-lg {
  width: 320px;
}

.field-main.is-full {
  width: 100%;
}

.field-control {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  min-width: 0;
}

/* 控件占满 field-main 的宽度，避免 n-input/n-select 默认 100% 撑破档位 */
/* 控件占满 field-main 的宽度：naive 的 select / input-number 默认不撑满，
   不写这一条就得回到内联 style="width: 200px" 的老路 */
.field-control > :deep(.n-input),
.field-control > :deep(.n-select),
.field-control > :deep(.n-input-number),
.field-control > :deep(.n-date-picker) {
  width: 100%;
}

.field-control > :deep(*) {
  min-width: 0;
}

.field-hint {
  margin: 0;
  font-size: var(--text-xs);
  line-height: var(--leading-normal);
  color: var(--text-2);
  overflow-wrap: anywhere;
}

@media (max-width: 767px) {
  .field-main.is-sm,
  .field-main.is-md,
  .field-main.is-lg {
    width: 100%;
  }
}
</style>
