import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

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

  constructor(url: string) {
    this.url = url
    FakeWebSocket.instances.push(this)
  }

  send(): void {}

  close(): void {
    this.readyState = FakeWebSocket.CLOSED
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
      location: { protocol: 'http:', host: 'localhost', pathname: '/' },
    })
    vi.stubGlobal('localStorage', createStorage())
    vi.stubGlobal('WebSocket', FakeWebSocket)
    vi.spyOn(console, 'log').mockImplementation(() => undefined)
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
})
