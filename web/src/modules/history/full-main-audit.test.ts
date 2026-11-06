import { beforeEach, expect, it, vi } from 'vitest'
import { ref } from 'vue'
import type { HistoryRecord } from './types'

const hooks = vi.hoisted(() => ({
  handler: null as ((message: unknown) => void) | null,
  listFolders: vi.fn(), create: vi.fn(), getOrganizeConfig: vi.fn(),
}))
vi.mock('vue', async importOriginal => ({
  ...await importOriginal<typeof import('vue')>(),
  onMounted: (callback: () => void) => callback(),
  onUnmounted: vi.fn(),
}))
vi.mock('naive-ui', () => ({ useMessage: () => ({ success: vi.fn(), warning: vi.fn(), error: vi.fn() }) }))
vi.mock('@/shared/api/watcher', () => ({ watcherApi: { listFolders: hooks.listFolders } }))
vi.mock('@/shared/api/config', () => ({ configApi: { getOrganizeConfig: hooks.getOrganizeConfig } }))
vi.mock('@/modules/scrape/api', () => ({ manualJobApi: { create: hooks.create } }))
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

it('keeps hexadecimal watcher IDs distinct in configuration reuse', async () => {
  hooks.listFolders.mockResolvedValue({ folders: [
    { id: '1abcdef0', path: '/media/a' }, { id: '1defabc0', path: '/media/b' },
  ] })
  const { useWatcherConfig } = await import('@/modules/scrape/hooks/useWatcherConfig')
  const config = useWatcherConfig()
  await config.loadWatchedFolders()
  const values = config.configReuseOptions.value.slice(1).map(option => option.value)
  expect(new Set(values).size).toBe(2)
})

it('starts a new task wizard at path selection after a successful creation', async () => {
  const { nextTick } = await import('vue')
  const { useTaskWizard } = await import('@/modules/scrape/hooks/useTaskWizard')
  hooks.listFolders.mockResolvedValue({ folders: [] })
  hooks.getOrganizeConfig.mockResolvedValue({ organize_dir: '/media/out' })
  hooks.create.mockResolvedValue({ id: 1 })
  const show = ref(true)
  const wizard = useTaskWizard({ show: () => show.value,
    initialScanPath: () => '', initialScanLocator: () => null,
    onSuccess: vi.fn(), onClose: () => { show.value = false },
  })
  wizard.formData.value.scan_path = '/media/in'
  wizard.formData.value.target_folder = '/media/out'
  wizard.currentStep.value = 3
  await wizard.handleSubmit()
  await nextTick()
  show.value = true
  await nextTick()
  expect(wizard.currentStep.value).toBe(1)
})
