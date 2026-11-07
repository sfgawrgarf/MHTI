import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createRenderer, defineComponent, h, nextTick, reactive, ref, type Component } from 'vue'
import type { HistoryRecord } from './types'

const mocks = vi.hoisted(() => ({
  listRecordFiles: vi.fn(), deleteRecordFiles: vi.fn(), listRecords: vi.fn(), getSummary: vi.fn(),
  create: vi.fn(), replace: vi.fn(), push: vi.fn(),
  route: { query: {} as Record<string, unknown> },
}))
vi.mock('./api', () => ({ historyApi: mocks }))
vi.mock('@/modules/history/api', () => ({ historyApi: mocks }))
vi.mock('@/modules/history', () => ({ historyApi: mocks }))
vi.mock('@/modules/scrape/api', () => ({ manualJobApi: { create: mocks.create } }))
vi.mock('@/shared/api/watcher', () => ({ watcherApi: { listFolders: vi.fn().mockResolvedValue({ folders: [] }) } }))
vi.mock('@/shared/api/config', () => ({ configApi: { getOrganizeConfig: vi.fn().mockResolvedValue({}) } }))
vi.mock('vue-router', () => ({
  RouterLink: 'a', useRoute: () => mocks.route,
  useRouter: () => ({ replace: mocks.replace, push: mocks.push }),
}))
vi.mock('@/stores/auth', () => ({ useAuthStore: () => ({ username: 'admin', avatar: null }) }))
vi.mock('@/router/menu', () => ({ buildMenuEntries: () => [], isNavActive: () => false }))
vi.mock('@/shared/composables/useMobileLayout', () => ({ useMobileLayout: () => ({ isMobile: ref(false), isTouchDevice: ref(true) }) }))
vi.mock('@/shared/composables/useTheme', () => ({ useTheme: () => ({ isDark: ref(false), toggleTheme: vi.fn() }) }))
vi.mock('naive-ui', async () => {
  const { defineComponent, h } = await import('vue')
  const widget = (name: string) => defineComponent({
    name, inheritAttrs: false,
    setup: (_, { attrs, slots }) => () => name === 'NModal' && attrs.show === false ? null
      : h(name === 'NButton' ? 'button' : 'section', attrs,
        Object.values(slots).flatMap(slot => slot?.() || [])),
  })
  return {
    NButton: widget('NButton'), NIcon: widget('NIcon'), NModal: widget('NModal'),
    NRadio: widget('NRadio'), NRadioGroup: widget('NRadioGroup'), NSpin: widget('NSpin'),
    NAvatar: widget('NAvatar'), NTooltip: widget('NTooltip'),
    NDropdown: defineComponent({ emits: ['select'], setup: (_, { emit }) => () =>
      h('button', { 'data-logout-menu': true, onClick: () => emit('select', 'logout') }, 'logout') }),
    useMessage: () => ({ error: vi.fn(), warning: vi.fn(), success: vi.fn() }),
  }
})

// A renderer that exercises real Vue component lifecycle and event bindings without a browser.
interface Node { tag: string; text: string; props: Record<string, unknown>; children: Node[]; parent: Node | null }
const node = (tag: string, text = ''): Node => ({ tag, text, props: {}, children: [], parent: null })
const insert = (child: Node, parent: Node, anchor: Node | null = null) => {
  if (child.parent) child.parent.children.splice(child.parent.children.indexOf(child), 1)
  child.parent = parent
  const index = anchor ? parent.children.indexOf(anchor) : -1
  parent.children.splice(index < 0 ? parent.children.length : index, 0, child)
}
const renderer = createRenderer<Node, Node>({
  patchProp: (element, key, _old, value) => { element.props[key] = value },
  insert, remove: element => {
    element.parent?.children.splice(element.parent.children.indexOf(element), 1)
    element.parent = null
  },
  createElement: tag => node(tag), createText: text => node('text', text), createComment: text => node('comment', text),
  setText: (element, text) => { element.text = text },
  setElementText: (element, text) => { element.text = text; element.children = [] },
  parentNode: element => element.parent,
  nextSibling: element => element.parent?.children[element.parent.children.indexOf(element) + 1] || null,
  insertStaticContent: (content, parent, anchor) => { const element = node('static', content); insert(element, parent, anchor); return [element, element] },
})
const roots: Node[] = []
function mount(component: Component, props: Record<string, unknown> = {}) {
  const root = node('root')
  roots.push(root)
  renderer.render(h(component, props), root)
  return root
}
const all = (root: Node): Node[] => [root, ...root.children.flatMap(all)]
const text = (root: Node): string => all(root).map(item => item.text).join(' ')
const flush = async () => { await Promise.resolve(); await nextTick(); await Promise.resolve(); await nextTick() }
function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>(done => { resolve = done })
  return { promise, resolve }
}
const listing = (id: string) => ({ record_id: id, folder_path: `/${id}`, files: [
  { path: `/${id}/video.mkv`, role: 'organized', exists: true, size: 1, deletable: true, reason: null },
] })

beforeEach(() => {
  vi.clearAllMocks()
  mocks.route = reactive({ query: {} })
  mocks.listRecords.mockResolvedValue({ records: [], total: 0 })
  mocks.replace.mockImplementation(({ query }) => { mocks.route.query = query; return Promise.resolve() })
})
afterEach(() => { roots.splice(0).forEach(root => renderer.render(null, root)); vi.unstubAllGlobals() })

it('keeps displayed deletion files and the submitted record bound through reversed responses', async () => {
  const { default: Modal } = await import('./components/RecordFilesModal.vue')
  const first = deferred<ReturnType<typeof listing>>()
  const second = deferred<ReturnType<typeof listing>>()
  mocks.listRecordFiles.mockImplementation((id: string) => id === 'a' ? first.promise : second.promise)
  mocks.deleteRecordFiles.mockResolvedValue({ deleted: 1 })
  const record = (id: string) => ({ id, folder_path: `/${id}` } as HistoryRecord)
  const root = mount(Modal, { show: true, record: record('a') })
  renderer.render(h(Modal, { show: false, record: record('a') }), root)
  await flush()
  renderer.render(h(Modal, { show: true, record: record('b') }), root)
  await flush()
  second.resolve(listing('b'))
  await flush()
  first.resolve(listing('a'))
  await flush()
  expect(text(root)).toContain('/b/video.mkv')
  expect(text(root)).not.toContain('/a/video.mkv')
  const button = all(root).find(item => item.tag === 'button' && text(item).includes('删除 1'))!
  expect(button.props.disabled).toBe(false)
  await (button.props.onClick as () => Promise<void>)()
  expect(mocks.deleteRecordFiles).toHaveBeenCalledExactlyOnceWith('b', 'all')
})

it('forwards avatar-menu logout through the top bar', async () => {
  const { default: TopBar } = await import('@/layouts/AppTopBar.vue')
  const logout = vi.fn()
  const root = mount(TopBar, { onLogout: logout })
  const menu = all(root).find(item => item.props['data-logout-menu'])!
  ;(menu.props.onClick as () => void)()
  expect(logout).toHaveBeenCalledOnce()
})

it('accepts real mouse and keyboard input on a touch-capable device', async () => {
  vi.stubGlobal('document', { body: {} })
  const { useTouchGesture } = await import('@/shared/composables/useTouchGesture')
  const onTap = vi.fn()
  const gesture = useTouchGesture({ clickable: () => true, disabled: () => false,
    touchFeedback: () => true, isTouchDevice: ref(true), onTap })
  const target = { tagName: 'DIV', parentElement: null, getAttribute: () => null, classList: { contains: () => false } }
  gesture.handleClick({ target, detail: 1, pointerType: 'mouse' } as unknown as MouseEvent)
  gesture.handleClick({ target, detail: 0 } as unknown as MouseEvent)
  gesture.handleClick({ target, detail: 1, pointerType: 'touch' } as unknown as MouseEvent)
  expect(onTap).toHaveBeenCalledTimes(2)
})

it('uses and follows URL status filters in history API requests', async () => {
  const { useHistoryList } = await import('./hooks/useHistoryList')
  mocks.route.query.status = 'success'
  let history!: ReturnType<typeof useHistoryList>
  mount(defineComponent({ setup: () => { history = useHistoryList(); return () => null } }))
  await history.loadRecords()
  expect(mocks.listRecords).toHaveBeenLastCalledWith(expect.objectContaining({ status: 'success' }))
  mocks.route.query.status = 'failed'
  await flush()
  expect(history.statusFilter.value).toBe('failed')
  expect(mocks.listRecords).toHaveBeenLastCalledWith(expect.objectContaining({ status: 'failed' }))
  history.handleStatusChange('all')
  await flush()
  expect(mocks.listRecords).toHaveBeenLastCalledWith(expect.objectContaining({ status: null }))
})

it('shows global home aggregates even when only ten recent records are loaded', async () => {
  const { useHomeStats } = await import('@/modules/home/hooks/useHomeStats')
  mocks.listRecords.mockResolvedValue({ records: [{ id: 'recent', status: 'failed' }], total: 100 })
  mocks.getSummary.mockResolvedValue({ total: 100, success: 80, failed: 20, weekly: [{ date: '2026-11-07', count: 100 }] })
  let home!: ReturnType<typeof useHomeStats>
  mount(defineComponent({ setup: () => { home = useHomeStats(); return () => null } }))
  await flush()
  expect(home.totalTasks.value).toBe(100)
  expect(home.successCount.value).toBe(80)
  expect(home.failedCount.value).toBe(20)
  expect(home.weeklyStats.value[0]?.value).toBe(100)
})

it('submits the selected file set with its IDs through the real task form', async () => {
  const { useManualJobForm } = await import('@/modules/scrape/hooks/useManualJobForm')
  const show = ref(false)
  const selected = [{ filename: 'a.mkv', path: '/115网盘/Inbox/a.mkv', size: 100, extension: '.mkv', mtime: null, file_id: '30', parent_id: '10' }]
  let form!: ReturnType<typeof useManualJobForm>
  mount(defineComponent({ setup: () => {
    form = useManualJobForm({ show: () => show.value, initialScanPath: () => '/115网盘/Inbox',
      initialScanLocator: () => ({ provider: '115', path: '/115网盘/Inbox', file_id: '10', is_dir: true }),
      selectedFiles: () => selected, onSuccess: vi.fn(), onClose: () => { show.value = false } })
    return () => null
  } }))
  show.value = true
  await flush()
  form.formData.value.target_folder = '/115网盘/Library'
  mocks.create.mockResolvedValue({ id: 1 })
  await form.handleSubmit()
  expect(mocks.create).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ selected_files: selected,
    scan_locator: expect.objectContaining({ file_id: '10' }) }))
})

it('delivers history events without retaining an unconsumed history payload queue', async () => {
  const browser = { location: { protocol: 'https:', host: 'test.example' },
    __WS_STATE__: undefined as undefined | { historyUpdates?: { value?: unknown[] } } }
  let socket: Socket | undefined
  class Socket {
    static OPEN = 1
    static CONNECTING = 0
    readyState = 0
    onmessage?: (event: { data: string }) => void
    close = vi.fn()
    constructor(_url: string) { socket = this }
  }
  vi.stubGlobal('window', browser)
  vi.stubGlobal('localStorage', { getItem: () => 'test-token' })
  vi.stubGlobal('WebSocket', Socket)
  const { useWebSocket } = await import('@/shared/composables/useWebSocket')
  let ws!: ReturnType<typeof useWebSocket>
  mount(defineComponent({ setup: () => { ws = useWebSocket(); return () => null } }))
  const handler = vi.fn()
  ws.registerHandler(handler)
  ws.connect()
  for (let index = 0; index < 1000; index++) {
    socket?.onmessage?.({ data: JSON.stringify({ type: 'history_created', payload: { id: `record-${index}` } }) })
  }
  expect(handler).toHaveBeenCalledTimes(1000)
  expect(browser.__WS_STATE__?.historyUpdates?.value?.length ?? 0).toBe(0)
  ws.unregisterHandler(handler)
  ws.disconnect()
})
