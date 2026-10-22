import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

const { verify, refresh, browse } = vi.hoisted(() => ({
  verify: vi.fn(), refresh: vi.fn(), browse: vi.fn(),
}))
vi.mock('@/modules/auth', () => ({ authApi: { verify } }))
vi.mock('@/shared/api/client', () => ({ refreshStoredAccessToken: refresh }))
vi.mock('@/shared/composables/useWebSocket', () => ({ useWebSocket: () => ({ connect: vi.fn() }) }))
vi.mock('@/shared/api/fs', () => ({ fsApi: { browse } }))

beforeEach(() => {
  vi.useFakeTimers()
  setActivePinia(createPinia())
  vi.clearAllMocks()
  const values = new Map<string, string>([
    ['access_token', 'access'], ['refresh_token', 'refresh'],
    ['session_id', 'session'], ['expires_at', String(Date.now() + 3600000)],
  ])
  vi.stubGlobal('localStorage', {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => values.set(key, value),
    removeItem: (key: string) => values.delete(key),
  })
})
afterEach(() => {
  vi.clearAllTimers()
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

it('bounds authentication retries when verify fails but refresh remains available', async () => {
  verify.mockRejectedValue({ response: { status: 503 }, message: 'verify unavailable' })
  // Stop after four rotations solely to make the existing infinite recursion observable and bounded.
  let rotations = 0
  refresh.mockImplementation(async () => {
    if (++rotations > 4) return null
    const accessToken = `rotated-${rotations}`
    localStorage.setItem('access_token', accessToken)
    localStorage.setItem('refresh_token', `refresh-${rotations}`)
    return { accessToken, expiresIn: 3600 }
  })
  const { useAuthStore } = await import('./auth')
  await useAuthStore().checkAuth()
  expect(verify.mock.calls.length).toBeLessThanOrEqual(2)
})

it('makes directories beyond the first page available in the folder picker', async () => {
  const all = Array.from({ length: 25 }, (_, i) => ({
    name: `dir-${i}`, path: `/media/dir-${i}`, is_dir: true, provider: 'local',
  }))
  browse.mockImplementation(async (_path: string, page: number, pageSize: number) => ({
    current_path: '/media', parent_path: '', total: all.length, page, page_size: pageSize,
    entries: all.slice((page - 1) * pageSize, page * pageSize),
  }))
  const { useFolderBrowser } = await import('@/shared/composables/useFolderBrowser')
  const picker = useFolderBrowser()
  await picker.loadDirectory('/media', 'local', null)
  // This picker exposes no page/next-page control: omitted directories cannot be selected.
  expect(picker.directories.value.map(entry => entry.path)).toContain('/media/dir-24')
})
