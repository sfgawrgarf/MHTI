import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const { post, responseUse, retryRequest } = vi.hoisted(() => ({
  post: vi.fn(), responseUse: vi.fn(), retryRequest: vi.fn(),
}))
vi.mock('axios', () => ({
  AxiosError: class extends Error {},
  default: {
    post,
    create: () => Object.assign(retryRequest, {
      defaults: { baseURL: '/api' },
      interceptors: { request: { use: vi.fn() }, response: { use: responseUse } },
    }),
  },
}))

beforeEach(() => {
  vi.resetModules()
  post.mockReset()
  responseUse.mockClear()
  retryRequest.mockReset().mockResolvedValue({ data: 'retried' })
  vi.stubGlobal('window', { location: { pathname: '/settings', href: '/settings' } })
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

  it.each(['success', 'failure'])('keeps a newer login after an older refresh %s', async (outcome) => {
    vi.useFakeTimers()
    const { ref } = await import('vue')
    const { createTokenManager } = await import('../../stores/authTokens')
    let release!: (value: { data: { access_token: string; refresh_token: string; expires_in: number } }) => void
    let reject!: (error: Error) => void
    let markStarted!: () => void
    const started = new Promise<void>(resolve => { markStarted = resolve })
    post.mockImplementation(() => {
      markStarted()
      return new Promise((resolve, rejectRequest) => { release = resolve; reject = rejectRequest })
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
      if (outcome === 'success') {
        release({ data: { access_token: 'late-access', refresh_token: 'late-refresh', expires_in: 3600 } })
      } else {
        reject(new Error('old refresh rejected'))
      }
      expect(await pending).toBe(true)
      expect(localStorage.getItem('refresh_token')).toBe('new-login-refresh')
      expect(localStorage.getItem('access_token')).toBe('new-login-access')
      expect(deps.isAuthenticated.value).toBe(true)
      expect(deps.sessionId.value).toBe('new-login-session')
      expect(deps.username.value).toBe('admin')
    } finally {
      manager.clearTokens()
      vi.useRealTimers()
    }
  })

  it.each(['login', 'logout'])('discards an already resolved refresh after a newer %s', async (action) => {
    vi.useFakeTimers()
    const { ref } = await import('vue')
    const { createTokenManager } = await import('../../stores/authTokens')
    const { refreshStoredAccessToken } = await import('./client')
    post.mockResolvedValue({ data: { access_token: 'rotated-access', refresh_token: 'rotated-refresh', expires_in: 3600 } })
    const deps = {
      expiresAt: ref<number | null>(null), sessionId: ref<string | null>(null),
      isAuthenticated: ref(true), username: ref<string | null>('admin'),
    }
    const manager = createTokenManager(deps)
    try {
      manager.setTokens('old-access', 'old', 'old-session', 3600)
      // This continuation runs after the API stored its result, but before
      // the token manager resumes from the same shared promise.
      const changed = refreshStoredAccessToken().then(() => {
        if (action === 'login') manager.setTokens('new-access', 'new-refresh', 'new-session', 7200)
        else manager.clearTokens()
      })
      const pending = manager.refreshAccessToken()
      await changed
      expect(await pending).toBe(action === 'login')
      expect(localStorage.getItem('access_token')).toBe(action === 'login' ? 'new-access' : null)
      expect(localStorage.getItem('refresh_token')).toBe(action === 'login' ? 'new-refresh' : null)
      expect(deps.sessionId.value).toBe(action === 'login' ? 'new-session' : null)
    } finally {
      manager.clearTokens()
      vi.useRealTimers()
    }
  })

  it.each(['success', 'failure'])('settles a current token-manager refresh %s', async (outcome) => {
    vi.useFakeTimers()
    const { ref } = await import('vue')
    const { createTokenManager } = await import('../../stores/authTokens')
    if (outcome === 'success') {
      post.mockResolvedValue({ data: { access_token: 'rotated-access', refresh_token: 'rotated-refresh', expires_in: 7200 } })
    } else {
      post.mockRejectedValue(new Error('refresh rejected'))
    }
    const deps = {
      expiresAt: ref<number | null>(null), sessionId: ref<string | null>(null),
      isAuthenticated: ref(true), username: ref<string | null>('admin'),
    }
    const manager = createTokenManager(deps)
    try {
      manager.setTokens('old-access', 'old', 'old-session', 3600)
      expect(await manager.refreshAccessToken()).toBe(outcome === 'success')
      expect(localStorage.getItem('access_token')).toBe(outcome === 'success' ? 'rotated-access' : null)
      expect(localStorage.getItem('refresh_token')).toBe(outcome === 'success' ? 'rotated-refresh' : null)
      expect(deps.isAuthenticated.value).toBe(outcome === 'success')
      expect(deps.username.value).toBe(outcome === 'success' ? 'admin' : null)
      expect(deps.expiresAt.value).toBe(outcome === 'success' ? Date.now() + 7200000 : null)
    } finally {
      manager.clearTokens()
      vi.useRealTimers()
    }
  })

  it.each(['success', 'failure'])('keeps a newer login when an interceptor refresh ends in %s', async (outcome) => {
    let release!: (value: unknown) => void
    let reject!: (error: Error) => void
    let markStarted!: () => void
    const started = new Promise<void>(resolve => { markStarted = resolve })
    post.mockImplementation(() => {
      markStarted()
      return new Promise((resolve, rejectRequest) => { release = resolve; reject = rejectRequest })
    })
    await import('./client')
    const rejected = responseUse.mock.calls[0]![1]!
    const error = { response: { status: 401 }, config: { url: '/protected', headers: { Authorization: 'Bearer old-access' } } }
    const pending = rejected(error).catch((value: unknown) => value)
    await started
    localStorage.setItem('access_token', 'new-login-access')
    localStorage.setItem('refresh_token', 'new-login-refresh')
    localStorage.setItem('session_id', 'new-login-session')
    if (outcome === 'success') release({ data: { access_token: 'late-access', refresh_token: 'late-refresh', expires_in: 3600 } })
    else reject(new Error('old refresh rejected'))
    expect(await pending).toBe(error)
    expect(localStorage.getItem('access_token')).toBe('new-login-access')
    expect(localStorage.getItem('refresh_token')).toBe('new-login-refresh')
    expect(window.location.href).toBe('/settings')
  })

  it('retries a delayed old-login 401 with the current token without rotating it', async () => {
    await import('./client')
    const rejected = responseUse.mock.calls[0]![1]!
    localStorage.setItem('access_token', 'new-login-access')
    localStorage.setItem('refresh_token', 'new-login-refresh')
    const config = { url: '/protected', headers: { Authorization: 'Bearer old-access' } }
    expect(await rejected({ response: { status: 401 }, config })).toEqual({ data: 'retried' })
    expect(config.headers.Authorization).toBe('Bearer new-login-access')
    expect(post).not.toHaveBeenCalled()
    expect(retryRequest).toHaveBeenCalledTimes(1)
    expect(localStorage.getItem('refresh_token')).toBe('new-login-refresh')
  })

  it('clears unchanged expired credentials when the interceptor refresh fails', async () => {
    post.mockRejectedValue(new Error('expired'))
    await import('./client')
    const rejected = responseUse.mock.calls[0]![1]!
    const error = { response: { status: 401 }, config: { url: '/protected', headers: { Authorization: 'Bearer old-access' } } }
    await expect(rejected(error)).rejects.toBe(error)
    expect(localStorage.getItem('access_token')).toBeNull()
    expect(localStorage.getItem('refresh_token')).toBeNull()
    expect(window.location.href).toBe('/login')
  })
})
