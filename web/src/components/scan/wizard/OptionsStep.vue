<script setup lang="ts">
import { computed } from 'vue'
import {
  NRadioGroup,
  NRadioButton,
  NSwitch,
  NIcon,
  NSpace,
  NTooltip,
  NDivider,
} from 'naive-ui'
import {
  LinkOutline,
  CopyOutline,
  SwapHorizontalOutline,
  DocumentOutline,
  ImageOutline,
  FilmOutline,
  TextOutline,
  SettingsOutline,
  InformationCircleOutline,
} from '@vicons/ionicons5'
import { LinkMode } from '@/api/types'
import type { ManualJobAdvancedSettings } from '@/api/types'

const props = defineProps<{
  linkMode: LinkMode
  deleteEmptyParent: boolean
  downloadPoster: boolean
  downloadBackdrop: boolean
  downloadThumbnail: boolean
  generateNfo: boolean
  processSubtitle: boolean
  overwriteExisting: boolean
  advancedSettings: ManualJobAdvancedSettings | null
}>()

const emit = defineEmits<{
  (e: 'update:linkMode', value: LinkMode): void
  (e: 'update:deleteEmptyParent', value: boolean): void
  (e: 'update:downloadPoster', value: boolean): void
  (e: 'update:downloadBackdrop', value: boolean): void
  (e: 'update:downloadThumbnail', value: boolean): void
  (e: 'update:generateNfo', value: boolean): void
  (e: 'update:processSubtitle', value: boolean): void
  (e: 'update:overwriteExisting', value: boolean): void
  (e: 'update:advancedSettings', value: ManualJobAdvancedSettings): void
}>()

// 链接模式选项
const linkModeOptions = [
  { label: '硬链接', value: LinkMode.HARDLINK, icon: LinkOutline, tip: '推荐，节省空间且保留原文件' },
  { label: '复制', value: LinkMode.COPY, icon: CopyOutline, tip: '复制文件，占用双倍空间' },
  { label: '移动', value: LinkMode.MOVE, icon: SwapHorizontalOutline, tip: '移动文件，原位置不保留' },
  { label: '软链接', value: LinkMode.SYMLINK, icon: DocumentOutline, tip: '符号链接，需原文件存在' },
]

// 当前选中的链接模式信息
const currentModeInfo = computed(() => {
  return linkModeOptions.find(opt => opt.value === props.linkMode)
})
</script>

<template>
  <div class="options-step">
    <!-- 文件整理模式 -->
    <div class="option-section">
      <div class="section-header">
        <NIcon :component="LinkOutline" class="section-icon" />
        <span class="section-title">文件整理模式</span>
        <NTooltip v-if="currentModeInfo">
          <template #trigger>
            <NIcon :component="InformationCircleOutline" class="info-icon" />
          </template>
          {{ currentModeInfo.tip }}
        </NTooltip>
      </div>
      <NRadioGroup
        :value="linkMode"
        class="link-mode-group"
        @update:value="emit('update:linkMode', $event)"
      >
        <NSpace :size="12">
          <NRadioButton
            v-for="option in linkModeOptions"
            :key="option.value"
            :value="option.value"
            class="link-mode-button"
          >
            <div class="mode-content">
              <NIcon :component="option.icon" />
              <span>{{ option.label }}</span>
            </div>
          </NRadioButton>
        </NSpace>
      </NRadioGroup>
    </div>

    <NDivider />

    <!-- 下载选项 -->
    <div class="option-section">
      <div class="section-header">
        <NIcon :component="ImageOutline" class="section-icon" />
        <span class="section-title">下载选项</span>
      </div>
      <div class="switch-grid">
        <div class="switch-item">
          <div class="switch-label">
            <span>下载海报</span>
            <span class="switch-desc">poster.jpg</span>
          </div>
          <NSwitch
            :value="downloadPoster"
            @update:value="emit('update:downloadPoster', $event)"
          />
        </div>
        <div class="switch-item">
          <div class="switch-label">
            <span>下载背景图</span>
            <span class="switch-desc">fanart.jpg</span>
          </div>
          <NSwitch
            :value="downloadBackdrop"
            @update:value="emit('update:downloadBackdrop', $event)"
          />
        </div>
        <div class="switch-item">
          <div class="switch-label">
            <span>下载剧集缩略图</span>
            <span class="switch-desc">episode-thumb.jpg</span>
          </div>
          <NSwitch
            :value="downloadThumbnail"
            @update:value="emit('update:downloadThumbnail', $event)"
          />
        </div>
      </div>
    </div>

    <NDivider />

    <!-- 其他选项 -->
    <div class="option-section">
      <div class="section-header">
        <NIcon :component="SettingsOutline" class="section-icon" />
        <span class="section-title">其他选项</span>
      </div>
      <div class="switch-grid">
        <div class="switch-item">
          <div class="switch-label">
            <NIcon :component="DocumentOutline" class="item-icon" />
            <span>生成 NFO 文件</span>
          </div>
          <NSwitch
            :value="generateNfo"
            @update:value="emit('update:generateNfo', $event)"
          />
        </div>
        <div class="switch-item">
          <div class="switch-label">
            <NIcon :component="TextOutline" class="item-icon" />
            <span>处理字幕文件</span>
          </div>
          <NSwitch
            :value="processSubtitle"
            @update:value="emit('update:processSubtitle', $event)"
          />
        </div>
        <div class="switch-item">
          <div class="switch-label">
            <NIcon :component="FilmOutline" class="item-icon" />
            <span>删除空父目录</span>
          </div>
          <NSwitch
            :value="deleteEmptyParent"
            @update:value="emit('update:deleteEmptyParent', $event)"
          />
        </div>
        <div class="switch-item warning">
          <div class="switch-label">
            <span>覆盖已存在文件</span>
            <span class="switch-desc">谨慎开启</span>
          </div>
          <NSwitch
            :value="overwriteExisting"
            @update:value="emit('update:overwriteExisting', $event)"
          />
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.options-step {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.option-section {
  padding: 4px 0;
}

.section-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 16px;
}

.section-icon {
  font-size: 18px;
  color: var(--ios-blue);
}

.section-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--ios-text-primary);
}

.info-icon {
  font-size: 14px;
  color: var(--ios-text-tertiary);
  cursor: help;
}

.link-mode-group {
  width: 100%;
}

.link-mode-group :deep(.n-radio-button) {
  flex: 1;
}

.mode-content {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 4px 0;
}

.switch-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 12px;
}

@media (max-width: 600px) {
  .switch-grid {
    grid-template-columns: 1fr;
  }
}

.switch-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  background: var(--ios-bg-tertiary);
  border-radius: 12px;
  transition: background 0.2s ease;
}

.switch-item:hover {
  background: var(--ios-bg-secondary);
}

.switch-item.warning {
  border: 1px solid var(--ios-orange);
  background: rgba(255, 149, 0, 0.08);
}

.switch-label {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.switch-label span:first-child {
  font-size: 14px;
  font-weight: 500;
  color: var(--ios-text-primary);
}

.switch-desc {
  font-size: 12px;
  color: var(--ios-text-tertiary);
}

.item-icon {
  font-size: 16px;
  color: var(--ios-text-secondary);
  margin-right: 6px;
}

:deep(.n-divider) {
  margin: 16px 0;
}

:deep(.n-switch.n-switch--active .n-switch__rail) {
  background-color: var(--ios-green);
}
</style>
