/**
 * WebSocket 客户端 - 实时接收刮削进度
 */
import { ref, computed } from 'vue'
import { getApiBaseUrl } from '@/api'

// WebSocket 消息类型
export interface WSMessage {
  type: string
  job_id?: string
  client_id?: string
  payload: any
  timestamp: string
}

// 事件处理器类型
type MessageHandler = (msg: WSMessage) => void

// 使用 window 存储全局单例，防止 HMR 导致多实例
interface WebSocketGlobalState {
  ws: WebSocket | null
  clientId: string | null
  reconnectTimer: ReturnType<typeof setTimeout> | null
  heartbeatTimer: ReturnType<typeof setInterval> | null
  reconnectEnabled: boolean
  isConnected: ReturnType<typeof ref<boolean>>
  handlers: Set<MessageHandler>
}

declare global {
  interface Window {
    __WS_STATE__?: WebSocketGlobalState
  }
}

// 初始化或获取全局状态
function getGlobalState(): WebSocketGlobalState {
  if (!window.__WS_STATE__) {
    window.__WS_STATE__ = {
      ws: null,
      clientId: null,
      reconnectTimer: null,
      heartbeatTimer: null,
      reconnectEnabled: false,
      isConnected: ref(false),
      handlers: new Set(),
    }
  }
  return window.__WS_STATE__
}

const state = getGlobalState()

function getWebSocketUrl(): string {
  const apiUrl = new URL(getApiBaseUrl(), window.location.origin)
  const protocol = apiUrl.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${protocol}//${apiUrl.host}/ws`
}

// 重连配置
const RECONNECT_DELAY = 3000
const HEARTBEAT_INTERVAL = 30000

// Handler 通知节流
let handlerBuffer: WSMessage[] = []
let handlerTimer: ReturnType<typeof setTimeout> | null = null
let handlerLastFlushTime = 0

// Handler 节流配置
const HANDLER_THROTTLE_MS = 100

/**
 * 通知所有处理器
 */
function notifyHandlers(msg: WSMessage): void {
  state.handlers.forEach((handler) => {
    try {
      handler(msg)
    } catch (e) {
      console.error('[WS] 处理器错误:', e)
    }
  })
}

/**
 * 批量通知处理器（用于 job_progress 消息）
 */
function flushHandlerNotifications(): void {
  handlerTimer = null
  if (handlerBuffer.length === 0) {
    return
  }

  // 只保留每个 job_id 的最新消息
  const latestMessages = new Map<string, WSMessage>()
  handlerBuffer.forEach((msg) => {
    if (msg.job_id) {
      latestMessages.set(msg.job_id, msg)
    }
  })

  latestMessages.forEach((msg) => {
    notifyHandlers(msg)
  })

  handlerBuffer = []
  handlerLastFlushTime = Date.now()
}

/**
 * 调度 handler 通知（节流）
 */
function scheduleHandlerNotification(msg: WSMessage): void {
  handlerBuffer.push(msg)
  if (!handlerTimer) {
    const timeSinceLastFlush = Date.now() - handlerLastFlushTime
    const delay = Math.max(0, HANDLER_THROTTLE_MS - timeSinceLastFlush)
    handlerTimer = setTimeout(flushHandlerNotifications, delay)
  }
}

function discardBufferedProgress(jobId?: string): void {
  if (!jobId) return
  handlerBuffer = handlerBuffer.filter((message) => message.job_id !== jobId)
  if (handlerBuffer.length === 0 && handlerTimer) {
    clearTimeout(handlerTimer)
    handlerTimer = null
  }
}

/**
 * 连接 WebSocket
 */
function connect(): void {
  // 防止重复连接：检查 OPEN 和 CONNECTING 状态
  if (state.ws?.readyState === WebSocket.OPEN || state.ws?.readyState === WebSocket.CONNECTING) {
    return
  }

  const token = localStorage.getItem('access_token')
  if (!token) {
    return
  }
  state.reconnectEnabled = true

  try {
    const socket = new WebSocket(getWebSocketUrl())
    state.ws = socket

    socket.onopen = () => {
      if (state.ws !== socket) return
      socket.send(JSON.stringify({ type: 'auth', token }))
    }

    socket.onclose = (event) => {
      // A close event can arrive after logout/login created a newer socket.
      // Stale callbacks must never reset the active connection or its heartbeat.
      if (state.ws !== socket) return
      state.ws = null
      console.log('[WS] 连接关闭')
      state.isConnected.value = false
      state.clientId = null
      stopHeartbeat()
      if (event.code === 4401) {
        state.reconnectEnabled = false
        localStorage.removeItem('access_token')
        localStorage.removeItem('refresh_token')
        localStorage.removeItem('session_id')
        localStorage.removeItem('expires_at')
        if (window.location.pathname !== '/login') {
          window.location.href = '/login'
        }
        return
      }
      scheduleReconnect()
    }

    socket.onerror = (error) => {
      if (state.ws !== socket) return
      console.error('[WS] 连接错误:', error)
    }

    socket.onmessage = (event) => {
      if (state.ws !== socket) return
      try {
        const msg: WSMessage = JSON.parse(event.data)
        handleMessage(msg)
      } catch (e) {
        console.error('[WS] 解析消息失败:', e)
      }
    }
  } catch (e) {
    console.error('[WS] 创建连接失败:', e)
    scheduleReconnect()
  }
}

/**
 * 处理收到的消息
 */
function handleMessage(msg: WSMessage): void {
  const { type, job_id, payload } = msg

  switch (type) {
    case 'connected':
      state.clientId = payload?.client_id || msg.client_id
      state.isConnected.value = true
      startHeartbeat()
      console.log('[WS] 客户端 ID:', state.clientId)
      break

    case 'pong':
      // 心跳响应，忽略
      break

    case 'job_created':
      console.log('[WS] 任务创建:', job_id)
      break

    case 'log':
      // 日志消息，可以在控制台输出或存储
      if (job_id && payload) {
        console.log(`[WS] [${job_id}] ${payload.level}: ${payload.message}`)
      }
      break

    // 历史记录详情页实时更新（由注册的 handler 处理，这里只做日志）
    case 'history_detail_update':
      console.log('[WS] 历史记录详情更新:', job_id, payload)
      break

    case 'history_detail_log':
      console.log('[WS] 历史记录详情日志:', job_id, payload)
      break
  }

  // 通知所有注册的处理器（对 job_progress 消息进行节流）
  if (type === 'job_progress') {
    // Only the registered consumers need progress updates. Coalesce them here
    // instead of retaining a second, unconsumed global progress cache.
    scheduleHandlerNotification(msg)
  } else {
    if (type === 'job_completed' || type === 'job_failed' || type === 'job_cancelled') {
      discardBufferedProgress(job_id)
    }
    // 其他消息立即通知
    notifyHandlers(msg)
  }
}

/**
 * 发送消息
 */
function send(data: object): boolean {
  if (state.ws?.readyState !== WebSocket.OPEN) {
    console.warn('[WS] 连接未就绪，无法发送消息')
    return false
  }
  state.ws.send(JSON.stringify(data))
  return true
}

/**
 * 订阅任务进度
 */
function subscribe(jobIds: string[]): void {
  send({ type: 'subscribe', job_ids: jobIds })
}

/**
 * 取消订阅
 */
function unsubscribe(jobIds: string[]): void {
  send({ type: 'unsubscribe', job_ids: jobIds })
}

/**
 * 启动心跳
 */
function startHeartbeat(): void {
  stopHeartbeat()
  state.heartbeatTimer = setInterval(() => {
    send({ type: 'ping' })
  }, HEARTBEAT_INTERVAL)
}

/**
 * 停止心跳
 */
function stopHeartbeat(): void {
  if (state.heartbeatTimer) {
    clearInterval(state.heartbeatTimer)
    state.heartbeatTimer = null
  }
}

/**
 * 安排重连
 */
function scheduleReconnect(): void {
  if (!state.reconnectEnabled) return
  if (!localStorage.getItem('access_token')) return
  if (state.reconnectTimer) return
  state.reconnectTimer = setTimeout(() => {
    state.reconnectTimer = null
    console.log('[WS] 尝试重连...')
    connect()
  }, RECONNECT_DELAY)
}

/**
 * 断开连接
 */
function disconnect(): void {
  state.reconnectEnabled = false
  if (state.reconnectTimer) {
    clearTimeout(state.reconnectTimer)
    state.reconnectTimer = null
  }
  stopHeartbeat()
  if (handlerTimer) {
    clearTimeout(handlerTimer)
    handlerTimer = null
  }
  handlerBuffer = []
  const socket = state.ws
  state.ws = null
  if (socket) {
    socket.close()
  }
  state.isConnected.value = false
  state.clientId = null
}

/**
 * WebSocket Composable
 */
export function useWebSocket() {
  // 注册消息处理器
  const registerHandler = (handler: MessageHandler) => {
    state.handlers.add(handler)
  }

  // 注销消息处理器
  const unregisterHandler = (handler: MessageHandler) => {
    state.handlers.delete(handler)
  }

  return {
    // 状态
    isConnected: state.isConnected,
    clientId: computed(() => state.clientId),

    // 方法
    connect,
    disconnect,
    subscribe,
    unsubscribe,
    send,

    // 处理器管理
    registerHandler,
    unregisterHandler,
  }
}
