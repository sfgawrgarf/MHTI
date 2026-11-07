<script setup lang="ts">
/**
 * 日志配置弹窗（8 字段 + 保存）
 *
 * 原先是一张独立卡片排在最前，每次进日志页得先滚过一张表单才看到日志；改成排在列表
 * 之后，列表回到首屏了，但要改配置仍得滚过整张表格。现在改为从记录卡头部「配置」按钮
 * 打开的弹窗——列表独占首屏，配置一步可达。
 *
 * 编辑走本地 draft（每次打开从 props.config 拷贝）：「取消」/ Esc / 点遮罩都丢弃草稿，
 * 只有「保存配置」把草稿交回父组件。配置实体与读写仍归父组件的 useLogConfig。
 */
import { computed, ref, watch } from 'vue'
import { NButton, NCard, NIcon, NInputNumber, NModal, NSelect, NSkeleton, NSwitch } from 'naive-ui'
import { AlertCircleOutline, CloseOutline } from '@vicons/ionicons5'
import type { LogConfig } from '@/shared/types/common'
import SettingsField from '@/modules/settings/components/SettingsField.vue'

const props = defineProps<{
  show: boolean
  config: LogConfig
  loading: boolean
  saving: boolean
  loadError?: string
  saveError?: string
}>()

const emit = defineEmits<{
  'update:show': [value: boolean]
  save: [draft: LogConfig]
  retry: []
}>()

const levelOptions = [
  { label: 'DEBUG', value: 'DEBUG' },
  { label: 'INFO', value: 'INFO' },
  { label: 'WARNING', value: 'WARNING' },
  { label: 'ERROR', value: 'ERROR' },
  { label: 'CRITICAL', value: 'CRITICAL' },
]

const draft = ref<LogConfig>({ ...props.config })

/** 每次打开都从当前配置重置草稿：上一次取消掉的改动不会残留 */
watch(
  () => props.show,
  (show) => {
    if (show) draft.value = { ...props.config }
  },
)

/** 字段级更新只改草稿，避免父组件里的配置被半成品覆盖 */
const set = <K extends keyof LogConfig>(key: K, value: LogConfig[K]) => {
  draft.value = { ...draft.value, [key]: value }
}

const close = () => emit('update:show', false)
const ready = computed(() => !props.loading && !props.loadError)
</script>

<template>
  <NModal
    :show="show"
    :mask-closable="false"
    transform-origin="center"
    @update:show="emit('update:show', $event)"
  >
    <NCard class="config-modal" :bordered="false">
      <template #header>
        <span class="modal-title">日志配置</span>
      </template>
      <template #header-extra>
        <NButton quaternary circle size="small" aria-label="关闭" @click="close">
          <template #icon>
            <NIcon :component="CloseOutline" />
          </template>
        </NButton>
      </template>

      <p class="modal-desc">记录级别、落盘策略与保留时长</p>

      <div v-if="loading" class="skeletons" aria-busy="true">
        <NSkeleton text style="width: 40%" />
        <NSkeleton text style="width: 65%" />
        <NSkeleton text style="width: 55%" />
      </div>

      <div v-else-if="loadError" class="error-bar" role="alert">
        <NIcon :component="AlertCircleOutline" :size="16" />
        <span class="error-text">{{ loadError }}</span>
        <NButton size="tiny" @click="emit('retry')">重试</NButton>
      </div>

      <div v-else class="field-grid">
        <SettingsField label="日志级别" hint="低于此级别的日志不记录" width="sm">
          <NSelect
            :value="draft.log_level"
            :options="levelOptions"
            :disabled="saving"
            @update:value="set('log_level', $event)"
          />
        </SettingsField>

        <SettingsField label="保留天数" hint="超过此天数的日志会被自动清理" width="sm">
          <NInputNumber
            :value="draft.db_retention_days"
            :min="1"
            :max="365"
            :disabled="saving"
            @update:value="set('db_retention_days', $event as number)"
          />
        </SettingsField>

        <SettingsField label="控制台输出" width="sm">
          <NSwitch
            :value="draft.console_enabled"
            :disabled="saving"
            @update:value="set('console_enabled', $event)"
          />
        </SettingsField>

        <SettingsField label="文件输出" width="sm">
          <NSwitch
            :value="draft.file_enabled"
            :disabled="saving"
            @update:value="set('file_enabled', $event)"
          />
        </SettingsField>

        <SettingsField label="数据库存储" width="sm">
          <NSwitch
            :value="draft.db_enabled"
            :disabled="saving"
            @update:value="set('db_enabled', $event)"
          />
        </SettingsField>

        <SettingsField label="日志页自动刷新" hint="开启后日志页定时获取新记录" width="sm">
          <NSwitch
            :value="draft.realtime_enabled"
            :disabled="saving"
            @update:value="set('realtime_enabled', $event)"
          />
        </SettingsField>

        <SettingsField label="单文件大小" hint="单位 MB，超出后轮转" width="sm">
          <NInputNumber
            :value="draft.max_file_size_mb"
            :min="1"
            :max="100"
            :disabled="saving"
            @update:value="set('max_file_size_mb', $event as number)"
          />
        </SettingsField>

        <SettingsField label="日志文件数量" hint="保留的轮转文件个数" width="sm">
          <NInputNumber
            :value="draft.max_file_count"
            :min="1"
            :max="20"
            :disabled="saving"
            @update:value="set('max_file_count', $event as number)"
          />
        </SettingsField>
      </div>

      <div v-if="saveError" class="error-bar is-save" role="alert">
        <NIcon :component="AlertCircleOutline" :size="16" />
        <span class="error-text">{{ saveError }}</span>
      </div>

      <template #footer>
        <div class="modal-footer">
          <NButton @click="close">取消</NButton>
          <NButton
            type="primary"
            :loading="saving"
            :disabled="!ready"
            @click="emit('save', draft)"
          >
            保存配置
          </NButton>
        </div>
      </template>
    </NCard>
  </NModal>
</template>

<style scoped>
.config-modal {
  width: 560px;
  max-width: calc(100vw - 32px);
  border-radius: var(--radius-lg);
  background: var(--bg-surface);
  box-shadow: var(--shadow-xl);
}

.modal-title {
  font-size: var(--text-lg);
  font-weight: 600;
  color: var(--text-1);
}

.modal-desc {
  margin: 0 0 var(--space-5);
  font-size: var(--text-sm);
  color: var(--text-3);
}

.field-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-4) var(--space-6);
}

.skeletons {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

/* 错误条语言与 SettingsSection 一致：左侧 2px 语义边而非色块底 */
.error-bar {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-3);
  border-left: 2px solid var(--danger-500);
  font-size: var(--text-sm);
  color: var(--danger-500);
}

.error-bar.is-save {
  margin-top: var(--space-4);
}

.error-text {
  flex: 1;
  min-width: 0;
  overflow-wrap: anywhere;
}

.modal-footer {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-3);
}

@media (max-width: 767px) {
  .field-grid {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
