<script setup lang="ts">
/**
 * 删除记录的源文件 / 刮削产物（物理删除的二次确认）
 *
 * 为什么要把路径一条条列出来：这是**不可恢复**的操作，用户必须在点确认前看到
 * 究竟哪些文件会被删掉。「删除刮削产物」听起来无害，但如果上次是 MOVE 模式，
 * 产物就是唯一的副本。
 *
 * 列表随范围选项切换：选「源文件」只看源文件，选「刮削产物」只看产物与元数据，
 * 否则用户勾了「只删产物」却看到源文件也在列表里，会以为源文件也保不住。
 *
 * 不可删除的路径（目录 / 越界 / 已不存在）同样列出来并写明原因，
 * 避免用户以为「点了就全删了」。
 */
import { computed, ref, watch } from 'vue'
import { NButton, NIcon, NModal, NRadio, NRadioGroup, NSpin, useMessage } from 'naive-ui'
import { AlertCircleOutline, CloseOutline } from '@vicons/ionicons5'
import { historyApi } from '@/modules/history/api'
import type {
  HistoryFileDeleteResponse,
  HistoryFileEntry,
  HistoryFileListResponse,
  HistoryFileRole,
  HistoryFileScope,
  HistoryRecord,
} from '@/modules/history/types'

const props = defineProps<{
  show: boolean
  record: HistoryRecord | null
}>()

const emit = defineEmits<{
  'update:show': [value: boolean]
  deleted: [response: HistoryFileDeleteResponse]
}>()

const loading = ref(false)
const message = useMessage()
const deleting = ref(false)
const listing = ref<HistoryFileListResponse | null>(null)
const scope = ref<HistoryFileScope>('all')

watch(
  () => [props.show, props.record?.id] as const,
  async ([show, recordId], _previous, onCleanup) => {
    let invalidated = false
    onCleanup(() => { invalidated = true })
    listing.value = null
    if (!show || !recordId) { loading.value = false; return }
    loading.value = true
    listing.value = null
    try {
      const response = await historyApi.listRecordFiles(recordId)
      if (!invalidated && props.show && props.record?.id === recordId && response.record_id === recordId) {
        listing.value = response
        scope.value = 'all'
      }
    } catch (error) {
      if (!invalidated) message.error('加载文件列表失败，请重试')
      console.error(error)
    } finally {
      if (!invalidated) loading.value = false
    }
  },
  { immediate: true }
)

const files = computed<HistoryFileEntry[]>(() => listing.value?.files ?? [])

/** 「刮削产物」= 整理后的视频 + 元数据（nfo / 图片），与后端 _ORGANIZED_ROLES 对齐 */
const ORGANIZED_ROLES: HistoryFileRole[] = ['organized', 'metadata']

/** 可删除的文件：源文件 / 产物分别统计，用于判断范围选项是否可用 */
const deletable = computed(() => files.value.filter((file) => file.deletable))
const deletableSources = computed(
  () => deletable.value.filter((file) => file.role === 'source').length
)
const deletableOrganized = computed(
  () => deletable.value.filter((file) => ORGANIZED_ROLES.includes(file.role)).length
)

/** 当前范围下要展示的文件：列表与删除目标保持同一口径 */
const visibleFiles = computed(() => {
  if (scope.value === 'source') return files.value.filter((file) => file.role === 'source')
  if (scope.value === 'organized')
    return files.value.filter((file) => ORGANIZED_ROLES.includes(file.role))
  return files.value
})

/** 当前范围下真正会被删掉的文件 */
const targets = computed(() => visibleFiles.value.filter((file) => file.deletable))

function formatSize(bytes: number): string {
  if (!bytes) return '—'
  const mb = bytes / 1024 / 1024
  return mb >= 1 ? `${mb.toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`
}

function roleLabel(role: HistoryFileEntry['role']): string {
  if (role === 'source') return '源文件'
  if (role === 'metadata') return '元数据'
  return '刮削产物'
}

async function handleDelete() {
  if (!props.show || !props.record || loading.value || listing.value?.record_id !== props.record.id || targets.value.length === 0 || deleting.value) return
  const recordId = props.record.id
  const displayed = listing.value
  deleting.value = true
  try {
    const response = await historyApi.deleteRecordFiles(recordId, scope.value)
    if (!props.show || props.record?.id !== recordId || listing.value !== displayed) return
    emit('deleted', response)
    if (response.deleted > 0) {
      emit('update:show', false)
    } else {
      // 一个都没删掉：留在弹窗里，让用户看到逐条原因
      const refreshed = await historyApi.listRecordFiles(recordId)
      if (props.show && props.record?.id === recordId && listing.value === displayed && refreshed.record_id === recordId) listing.value = refreshed
    }
  } catch (error) {
    message.error('删除失败，请刷新文件列表后重试')
    console.error(error)
  } finally {
    deleting.value = false
  }
}
</script>

<template>
  <NModal
    :show="show"
    :mask-closable="false"
    transform-origin="center"
    @update:show="emit('update:show', $event)"
  >
    <div class="files" role="dialog" aria-modal="true" aria-label="删除文件">
      <header class="files-head">
        <h2 class="files-title">删除文件</h2>
        <button type="button" class="icon-btn" aria-label="关闭" @click="emit('update:show', false)">
          <NIcon :component="CloseOutline" :size="17" />
        </button>
      </header>

      <div class="files-body">
        <p class="files-path">{{ record?.folder_path }}</p>

        <NSpin :show="loading">
          <NRadioGroup v-model:value="scope" size="small" :disabled="loading">
            <!-- 没有源文件时整个选项不显示：留着一个恒为 0 的选项只会让人以为"能不能删"还没加载出来 -->
            <NRadio v-if="deletableSources > 0" value="source">
              源文件（{{ deletableSources }}）
            </NRadio>
            <NRadio value="organized" :disabled="deletableOrganized === 0">
              刮削产物（{{ deletableOrganized }}）
            </NRadio>
            <NRadio value="all" :disabled="deletable.length === 0">
              两者都删（{{ deletable.length }}）
            </NRadio>
          </NRadioGroup>

          <ul v-if="visibleFiles.length > 0" class="file-list">
            <li v-for="file in visibleFiles" :key="`${file.role}:${file.path}`" class="file-item">
              <span class="file-role">{{ roleLabel(file.role) }}</span>
              <span class="file-path" :title="file.path">{{ file.path }}</span>
              <span class="file-size tabular">{{ formatSize(file.size) }}</span>
              <span v-if="!file.deletable" class="file-reason">{{ file.reason }}</span>
            </li>
          </ul>
          <p v-else-if="!loading" class="files-empty">这条记录没有可定位的源文件或产物。</p>
        </NSpin>
      </div>

      <footer class="files-foot">
        <p class="files-warning">
          <NIcon :component="AlertCircleOutline" :size="14" />
          物理删除，不进回收站，删除后无法恢复。
        </p>
        <div class="files-actions">
          <NButton @click="emit('update:show', false)">取消</NButton>
          <NButton
            type="error"
            :disabled="loading || targets.length === 0 || listing?.record_id !== record?.id"
            :loading="deleting"
            @click="handleDelete"
          >
            删除 {{ targets.length }} 个文件
          </NButton>
        </div>
      </footer>
    </div>
  </NModal>
</template>

<style scoped>
.files {
  display: flex;
  flex-direction: column;
  width: 560px;
  max-width: 95vw;
  max-height: 88vh;
  background: var(--bg-surface);
  border-radius: var(--radius-xl);
  box-shadow: var(--shadow-xl);
}

.files-head {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-4) var(--space-5);
  border-bottom: 1px solid var(--border-1);
}

.files-title {
  flex: 1;
  font-size: var(--text-md);
  font-weight: var(--weight-semibold);
  color: var(--text-1);
}

.icon-btn {
  display: grid;
  place-items: center;
  width: 28px;
  height: 28px;
  border: none;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--text-2);
  cursor: pointer;
  transition: background-color var(--duration-fast) var(--ease-in-out);
}

.icon-btn:hover {
  background: var(--bg-hover);
  color: var(--text-1);
}

.files-body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: var(--space-4) var(--space-5);
}

.files-path {
  margin-bottom: var(--space-3);
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  color: var(--text-3);
  word-break: break-all;
}

.file-list {
  /* 显式清掉浏览器给 ul 的默认内边距与列表标记：不清则整块列表右移 40px，
     与上面的路径、单选组对不齐 */
  margin: var(--space-3) 0 0;
  padding: 0;
  list-style: none;
  border-top: 1px solid var(--border-1);
}

.file-item {
  display: flex;
  align-items: baseline;
  gap: var(--space-2);
  padding: var(--space-2) 0;
  border-bottom: 1px solid var(--border-1);
  font-size: var(--text-sm);
}

.file-role {
  flex-shrink: 0;
  width: 60px;
  font-size: var(--text-xs);
  color: var(--text-2);
}

.file-path {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--text-1);
  direction: rtl;
  text-align: left;
}

.file-size {
  flex-shrink: 0;
  font-size: var(--text-xs);
  color: var(--text-2);
}

/* 不可删是「说明」不是「错误」，用中性文字，避免整列飘红 */
.file-reason {
  flex-shrink: 0;
  font-size: var(--text-xs);
  color: var(--text-3);
}

.files-empty {
  margin-top: var(--space-3);
  font-size: var(--text-sm);
  color: var(--text-2);
}

.files-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-4) var(--space-5);
  border-top: 1px solid var(--border-1);
}

.files-warning {
  display: flex;
  align-items: center;
  gap: var(--space-1);
  font-size: var(--text-xs);
  color: var(--danger-500);
}

.files-actions {
  display: flex;
  gap: var(--space-2);
}

@media (max-width: 767px) {
  .files-foot {
    flex-direction: column;
    align-items: stretch;
  }

  .files-actions {
    justify-content: flex-end;
  }
}
</style>
