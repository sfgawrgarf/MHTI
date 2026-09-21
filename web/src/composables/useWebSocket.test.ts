import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const apiMocks = vi.hoisted(() => ({
  api: { defaults: { baseURL: '/api' } },
  refreshStoredAccessToken: vi.fn(),
  clearStoredAuthTokens: vi.fn(),
}))

vi.mock('@/api', () => ({
  default: apiMocks.api,
  getApiBaseUrl: () => apiMocks.api.defaults.baseURL,
  refreshStoredAccessToken: apiMocks.refreshStoredAccessToken,
  clearStoredAuthTokens: apiMocks.clearStoredAuthTokens,
}))

type SocketHandler<T> = ((event: T) => void) | null
type TestMessage = { type: string; job_id?: string; payload: any }

class FakeWebSocket {
  static readonly CONNECTING = 0
  static readonly OPEN = 1
  static readonly CLOSED = 3
  static instances: FakeWebSocket[] = []

  readyState = FakeWebSocket.CONNECTING
  onopen: SocketHandler<Event> = null
  onclose: SocketHandler<CloseEvent> = null
  onerror: SocketHandler<Event> = null
  onmessage: SocketHandler<MessageEvent> = null
  readonly url: string
  sent: string[] = []

  constructor(url: string) {
    this.url = url
    FakeWebSocket.instances.push(this)
  }

  send(data: string): void {
    this.sent.push(data)
  }

  close(): void {
    this.readyState = FakeWebSocket.CLOSED
    this.onclose?.({ code: 1000 } as CloseEvent)
  }

  receive(message: object): void {
    this.onmessage?.({ data: JSON.stringify(message) } as MessageEvent)
  }
}

function createStorage() {
  const values = new Map<string, string>([['access_token', 'test-token']])
  return {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => values.set(key, value),
    removeItem: (key: string) => values.delete(key),
    clear: () => values.clear(),
  }
}

describe('useWebSocket message lifecycle', () => {
  beforeEach(() => {
    vi.resetModules()
    vi.useFakeTimers()
    FakeWebSocket.instances = []
    vi.stubGlobal('window', {
      location: {
        protocol: 'http:',
        host: 'localhost',
        origin: 'http://localhost',
        pathname: '/',
      },
    })
    vi.stubGlobal('localStorage', createStorage())
    vi.stubGlobal('WebSocket', FakeWebSocket)
    vi.spyOn(console, 'log').mockImplementation(() => undefined)
    apiMocks.api.defaults.baseURL = '/api'
    apiMocks.refreshStoredAccessToken.mockReset().mockResolvedValue(null)
    apiMocks.clearStoredAuthTokens.mockReset().mockImplementation(() => {
      for (const key of ['access_token', 'refresh_token', 'session_id', 'expires_at']) {
        localStorage.removeItem(key)
      }
    })
  })

  afterEach(() => {
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
    vi.useRealTimers()
  })

  it('does not expose unused global message caches', async () => {
    const { useWebSocket } = await import('./useWebSocket')
    const client = useWebSocket()

    expect(client).not.toHaveProperty('historyUpdates')
    expect(client).not.toHaveProperty('jobProgress')
    expect(client).not.toHaveProperty('pendingActions')
  })

  it('coalesces progress notifications and keeps the latest job update', async () => {
    const { useWebSocket } = await import('./useWebSocket')
    const client = useWebSocket()
    const handler = vi.fn<(message: TestMessage) => void>()
    client.registerHandler(handler)
    client.connect()
    const socket = FakeWebSocket.instances[0]!

    socket.receive({ type: 'job_progress', job_id: 'one', payload: { progress: 10 } })
    socket.receive({ type: 'job_progress', job_id: 'one', payload: { progress: 90 } })
    socket.receive({ type: 'job_progress', job_id: 'two', payload: { progress: 50 } })

    expect(handler).not.toHaveBeenCalled()
    vi.advanceTimersByTime(100)

    expect(handler).toHaveBeenCalledTimes(2)
    expect(handler.mock.calls[0]![0].payload.progress).toBe(90)
    expect(handler.mock.calls[1]![0].job_id).toBe('two')
  })

  it('delivers cancellation immediately and discards buffered progress on disconnect', async () => {
    const { useWebSocket } = await import('./useWebSocket')
    const client = useWebSocket()
    const handler = vi.fn<(message: TestMessage) => void>()
    client.registerHandler(handler)
    client.connect()
    const socket = FakeWebSocket.instances[0]!

    socket.receive({ type: 'job_progress', job_id: 'one', payload: { progress: 25 } })
    socket.receive({ type: 'job_cancelled', job_id: 'one', payload: { status: 'cancelled' } })

    expect(handler).toHaveBeenCalledTimes(1)
    expect(handler.mock.calls[0]![0].type).toBe('job_cancelled')

    client.disconnect()
    vi.advanceTimersByTime(100)
    expect(handler).toHaveBeenCalledTimes(1)
  })

  it('does not deliver buffered progress after a terminal message', async () => {
    const { useWebSocket } = await import('./useWebSocket')
    const client = useWebSocket()
    const handler = vi.fn<(message: TestMessage) => void>()
    client.registerHandler(handler)
    client.connect()
    const socket = FakeWebSocket.instances[0]!

    socket.receive({ type: 'job_progress', job_id: 'one', payload: { progress: 90 } })
    socket.receive({ type: 'job_completed', job_id: 'one', payload: { status: 'success' } })
    vi.advanceTimersByTime(100)

    expect(handler).toHaveBeenCalledTimes(1)
    expect(handler.mock.calls[0]![0].type).toBe('job_completed')
  })

  it('does not reconnect after an intentional disconnect', async () => {
    const { useWebSocket } = await import('./useWebSocket')
    const client = useWebSocket()
    client.connect()

    client.disconnect()
    vi.advanceTimersByTime(3000)

    expect(FakeWebSocket.instances).toHaveLength(1)
  })

  it('uses the runtime API origin for WebSocket connections', async () => {
    const { default: api } = await import('@/api')
    api.defaults.baseURL = 'https://api.example.test/mhti/api'
    const { useWebSocket } = await import('./useWebSocket')

    useWebSocket().connect()

    expect(FakeWebSocket.instances[0]!.url).toBe('wss://api.example.test/mhti/ws')
  })

  it('ignores a stale close event after a newer connection is established', async () => {
    const { useWebSocket } = await import('./useWebSocket')
    const client = useWebSocket()
    client.connect()
    const oldSocket = FakeWebSocket.instances[0]!

    client.disconnect()
    client.connect()
    const newSocket = FakeWebSocket.instances[1]!
    newSocket.readyState = FakeWebSocket.OPEN
    newSocket.receive({ type: 'connected', payload: { client_id: 'new-client' } })

    oldSocket.onclose?.({ code: 1000 } as CloseEvent)

    expect(client.isConnected.value).toBe(true)
    expect(client.clientId.value).toBe('new-client')
    vi.advanceTimersByTime(30000)
    expect(newSocket.sent).toContain(JSON.stringify({ type: 'ping' }))
  })

  it('refreshes an expired access token and reconnects without clearing the session', async () => {
    localStorage.setItem('refresh_token', 'valid-refresh')
    apiMocks.refreshStoredAccessToken.mockResolvedValue({
      accessToken: 'new-access',
      expiresIn: 900,
    })
    const { useWebSocket } = await import('./useWebSocket')
    const client = useWebSocket()
    client.connect()

    FakeWebSocket.instances[0]!.onclose?.({ code: 4401 } as CloseEvent)
    await Promise.resolve()
    await Promise.resolve()

    expect(apiMocks.refreshStoredAccessToken).toHaveBeenCalledTimes(1)
    expect(apiMocks.clearStoredAuthTokens).not.toHaveBeenCalled()
    expect(localStorage.getItem('refresh_token')).toBe('valid-refresh')
    expect(FakeWebSocket.instances).toHaveLength(2)
  })

  it('clears the session only after websocket authentication recovery fails', async () => {
    localStorage.setItem('refresh_token', 'expired-refresh')
    const { useWebSocket } = await import('./useWebSocket')
    const client = useWebSocket()
    client.connect()

    FakeWebSocket.instances[0]!.onclose?.({ code: 4401 } as CloseEvent)
    await Promise.resolve()
    await Promise.resolve()

    expect(apiMocks.refreshStoredAccessToken).toHaveBeenCalledTimes(1)
    expect(apiMocks.clearStoredAuthTokens).toHaveBeenCalledTimes(1)
    expect(localStorage.getItem('access_token')).toBeNull()
    expect(window.location.href).toBe('/login')
    expect(FakeWebSocket.instances).toHaveLength(1)
  })

  it('ignores a completed authentication refresh after an explicit disconnect', async () => {
    let finishRefresh!: (value: { accessToken: string; expiresIn: number }) => void
    apiMocks.refreshStoredAccessToken.mockReturnValue(
      new Promise((resolve) => {
        finishRefresh = resolve
      })
    )
    const { useWebSocket } = await import('./useWebSocket')
    const client = useWebSocket()
    client.connect()

    FakeWebSocket.instances[0]!.onclose?.({ code: 4401 } as CloseEvent)
    client.disconnect()
    finishRefresh({ accessToken: 'late-access', expiresIn: 900 })
    await Promise.resolve()
    await Promise.resolve()

    expect(apiMocks.clearStoredAuthTokens).not.toHaveBeenCalled()
    expect(FakeWebSocket.instances).toHaveLength(1)
  })
})
