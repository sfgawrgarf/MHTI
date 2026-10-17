import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const { post } = vi.hoisted(() => ({ post: vi.fn() }))
vi.mock('axios', () => ({
  AxiosError: class extends Error {},
  default: {
    post,
    create: () => ({
      defaults: { baseURL: '/api' },
      interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
    }),
  },
}))

beforeEach(() => {
  vi.resetModules()
  post.mockReset()
  const values = new Map<string, string>([
    ['refresh_token', 'old'], ['access_token', 'old-access'],
    ['expires_at', String(Date.now() + 1000)],
  ])
  vi.stubGlobal('localStorage', {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => values.set(key, value),
    removeItem: (key: string) => values.delete(key),
  })
  let queue = Promise.resolve()
  vi.stubGlobal('navigator', {
    locks: {
      request: (_name: string, _options: unknown, callback: () => Promise<unknown>) => {
        const next = queue.then(callback)
        queue = next.then(() => undefined, () => undefined)
        return next
      },
    },
  })
})
afterEach(() => vi.unstubAllGlobals())

describe('cross-tab refresh', () => {
  it('rotates once across separate module instances sharing storage', async () => {
    post.mockResolvedValue({ data: { access_token: 'new-access', refresh_token: 'new', expires_in: 3600 } })
    const firstTab = await import('./client')
    vi.resetModules()
    const secondTab = await import('./client')
    const results = await Promise.all([
      firstTab.refreshStoredAccessToken(), secondTab.refreshStoredAccessToken(),
    ])
    expect(post).toHaveBeenCalledTimes(1)
    expect(results.map(result => result?.accessToken)).toEqual(['new-access', 'new-access'])
    expect(post).toHaveBeenCalledWith('/api/auth/refresh', { refresh_token: 'old' }, { timeout: 15000 })
  })

  it('does not restore credentials after logout during a request', async () => {
    post.mockImplementation(async () => {
      localStorage.removeItem('refresh_token')
      localStorage.removeItem('access_token')
      return { data: { access_token: 'new-access', refresh_token: 'new', expires_in: 3600 } }
    })
    const { refreshStoredAccessToken } = await import('./client')
    expect(await refreshStoredAccessToken()).toBeNull()
    expect(localStorage.getItem('access_token')).toBeNull()
  })

  it('does not rotate without an origin-wide lock', async () => {
    vi.stubGlobal('navigator', {})
    const { refreshStoredAccessToken } = await import('./client')
    expect(await refreshStoredAccessToken()).toBeNull()
    expect(post).not.toHaveBeenCalled()
  })

  it('keeps a newer login when an older token-manager refresh completes', async () => {
    vi.useFakeTimers()
    const { ref } = await import('vue')
    const { createTokenManager } = await import('../../stores/authTokens')
    let release!: (value: { data: { access_token: string; refresh_token: string; expires_in: number } }) => void
    let markStarted!: () => void
    const started = new Promise<void>(resolve => { markStarted = resolve })
    post.mockImplementation(() => {
      markStarted()
      return new Promise(resolve => { release = resolve })
    })
    const deps = {
      expiresAt: ref<number | null>(null), sessionId: ref<string | null>('old-session'),
      isAuthenticated: ref(true), username: ref<string | null>('admin'),
    }
    const manager = createTokenManager(deps)
    try {
      manager.setTokens('old-access', 'old', 'old-session', 3600)
      const pending = manager.refreshAccessToken()
      await started
      // A successful new login can happen in this tab or another tab sharing storage.
      manager.setTokens('new-login-access', 'new-login-refresh', 'new-login-session', 3600)
      release({ data: { access_token: 'late-access', refresh_token: 'late-refresh', expires_in: 3600 } })
      await pending
      expect(localStorage.getItem('refresh_token')).toBe('new-login-refresh')
      expect(localStorage.getItem('access_token')).toBe('new-login-access')
      expect(deps.isAuthenticated.value).toBe(true)
    } finally {
      manager.clearTokens()
      vi.useRealTimers()
    }
  })
})
