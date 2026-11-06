import { beforeEach, expect, it, vi } from 'vitest'
import { ref } from 'vue'
import type { HistoryRecord } from './types'

const hooks = vi.hoisted(() => ({ handler: null as ((message: unknown) => void) | null }))
vi.mock('vue', async importOriginal => ({
  ...await importOriginal<typeof import('vue')>(),
  onMounted: (callback: () => void) => callback(),
  onUnmounted: vi.fn(),
}))
vi.mock('naive-ui', () => ({ useMessage: () => ({ success: vi.fn() }) }))
vi.mock('@/shared/composables/useWebSocket', () => ({
  useWebSocket: () => ({
    registerHandler: (handler: typeof hooks.handler) => { hooks.handler = handler },
    unregisterHandler: vi.fn(),
  }),
}))

beforeEach(() => { hooks.handler = null })

it('keeps another manual task out of a task-filtered history list', async () => {
  const { useHistoryRealtime } = await import('./hooks/useHistoryRealtime')
  const records = ref<HistoryRecord[]>([{ id: 'task-a', manual_job_id: 1 } as HistoryRecord])
  useHistoryRealtime({ records, total: ref(1), loading: ref(false), reload: vi.fn() })
  hooks.handler?.({ type: 'history_created', payload: { id: 'task-b', manual_job_id: 2 } })
  expect(records.value.map(record => record.id)).toEqual(['task-a'])
})
