/**
 * WebSocket 客户端 - 实时接收刮削进度
 */
import { ref, reactive, onUnmounted, computed } from 'vue'

// WebSocket 消息类型
export interface WSMessage {
  type: string
  job_id?: string
  client_id?: string
  payload: any
  timestamp: string
}

// 任务进度信息
export interface JobProgress {
  step: string
  progress: number
  message: string
}

// 需要用户操作的信息
export interface NeedActionInfo {
  job_id: string
  action_type: string
  options: any
}

// 历史记录更新信息
export interface HistoryUpdate {
  type: 'created' | 'updated' | 'deleted' | 'cleared'
  record?: any
  id?: string
  updates?: any
  count?: number
}

// 历史记录详情更新信息（用于详情页实时刷新）
export interface HistoryDetailUpdate {
  record_id: string
  status?: string
  progress?: number
  logs?: any[]
  [key: string]: any
}

// 历史记录详情日志步骤
export interface HistoryDetailLogStep {
  name: string
  completed: boolean
  logs: Array<{ level: string; message: string }>
}

// 事件处理器类型
type MessageHandler = (msg: WSMessage) => void

// 使用 window 存储全局单例，防止 HMR 导致多实例
interface WebSocketGlobalState {
  ws: WebSocket | null
  clientId: string | null
  reconnectTimer: ReturnType<typeof setTimeout> | null
  heartbeatTimer: ReturnType<typeof setInterval> | null
  isConnected: ReturnType<typeof ref<boolean>>
  jobProgress: Map<string, JobProgress>
  pendingActions: Map<string, NeedActionInfo>
  handlers: Set<MessageHandler>
  historyUpdates: ReturnType<typeof ref<HistoryUpdate[]>>
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
      isConnected: ref(false),
      jobProgress: reactive(new Map()),
      pendingActions: reactive(new Map()),
      handlers: new Set(),
      historyUpdates: ref([]),
    }
  }
  return window.__WS_STATE__
}

const state = getGlobalState()

// WebSocket 服务器地址 - 通过 Nginx 代理连接，自动适配协议和端口
const WS_URL = `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}/ws`

// 重连配置
const RECONNECT_DELAY = 3000
const HEARTBEAT_INTERVAL = 30000

// 进度更新节流配置
const PROGRESS_THROTTLE_MS = 200
let progressBuffer: Map<string, JobProgress> = new Map()
let rafId: number | null = null
let lastFlushTime = 0

/**
 * 批量刷新进度更新到 state
 */
function flushProgressUpdates(): void {
  if (progressBuffer.size === 0) {
    rafId = null
    return
  }

  progressBuffer.forEach((progress, jobId) => {
    state.jobProgress.set(jobId, progress)
  })
  progressBuffer.clear()
  lastFlushTime = Date.now()
  rafId = null
}

/**
 * 节流的进度更新
 */
function throttledProgressUpdate(jobId: string, payload: JobProgress): void {
  progressBuffer.set(jobId, payload)

  // 使用 requestAnimationFrame 批量更新，避免阻塞 UI
  if (!rafId) {
    const timeSinceLastFlush = Date.now() - lastFlushTime
    if (timeSinceLastFlush >= PROGRESS_THROTTLE_MS) {
      // 立即刷新
      rafId = requestAnimationFrame(flushProgressUpdates)
    } else {
      // 延迟刷新
      rafId = requestAnimationFrame(() => {
        setTimeout(flushProgressUpdates, PROGRESS_THROTTLE_MS - timeSinceLastFlush)
      })
    }
  }
}

// Handler 通知节流
let handlerBuffer: WSMessage[] = []
let handlerRafId: number | null = null
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
  if (handlerBuffer.length === 0) {
    handlerRafId = null
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
  handlerRafId = null
  handlerLastFlushTime = Date.now()
}

/**
 * 调度 handler 通知（节流）
 */
function scheduleHandlerNotification(msg: WSMessage): void {
  handlerBuffer.push(msg)
  if (!handlerRafId) {
    const timeSinceLastFlush = Date.now() - handlerLastFlushTime
    if (timeSinceLastFlush >= HANDLER_THROTTLE_MS) {
      // 立即刷新
      handlerRafId = requestAnimationFrame(flushHandlerNotifications)
    } else {
      // 延迟刷新
      handlerRafId = requestAnimationFrame(() => {
        setTimeout(flushHandlerNotifications, HANDLER_THROTTLE_MS - timeSinceLastFlush)
      })
    }
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

  try {
    state.ws = new WebSocket(WS_URL)

    state.ws.onopen = () => {
      state.ws?.send(JSON.stringify({ type: 'auth', token }))
    }

    state.ws.onclose = (event) => {
      console.log('[WS] 连接关闭')
      state.isConnected.value = false
      state.clientId = null
      stopHeartbeat()
      if (event.code === 4401) {
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

    state.ws.onerror = (error) => {
      console.error('[WS] 连接错误:', error)
    }

    state.ws.onmessage = (event) => {
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

    case 'job_progress':
      if (job_id && payload) {
        // 使用节流更新，避免频繁触发 Vue 响应式更新导致 UI 卡死
        throttledProgressUpdate(job_id, payload as JobProgress)
      }
      break

    case 'job_completed':
      if (job_id) {
        state.jobProgress.delete(job_id)
        state.pendingActions.delete(job_id)
      }
      break

    case 'job_failed':
      if (job_id) {
        state.jobProgress.delete(job_id)
        state.pendingActions.delete(job_id)
      }
      break

    case 'need_action':
      if (job_id && payload) {
        state.pendingActions.set(job_id, {
          job_id,
          action_type: payload.action_type,
          options: payload.options,
        })
      }
      break

    case 'log':
      // 日志消息，可以在控制台输出或存储
      if (job_id && payload) {
        console.log(`[WS] [${job_id}] ${payload.level}: ${payload.message}`)
      }
      break

    // 历史记录相关消息
    case 'history_created':
      state.historyUpdates.value?.push({ type: 'created', record: payload })
      break

    case 'history_updated':
      state.historyUpdates.value?.push({ type: 'updated', id: payload.id, updates: payload })
      break

    case 'history_deleted':
      state.historyUpdates.value?.push({ type: 'deleted', id: payload.id })
      break

    case 'history_cleared':
      state.historyUpdates.value?.push({ type: 'cleared', count: payload.count })
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
    // job_progress 消息已通过 throttledProgressUpdate 处理，
    // 对 handlers 也进行节流，避免频繁调用
    scheduleHandlerNotification(msg)
  } else {
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
 * 发送用户操作响应
 */
function sendUserAction(jobId: string, actionType: string, selection: any): void {
  send({
    type: 'user_action',
    job_id: jobId,
    action_type: actionType,
    selection,
  })
  // 清除待处理操作
  state.pendingActions.delete(jobId)
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
  if (state.reconnectTimer) {
    clearTimeout(state.reconnectTimer)
    state.reconnectTimer = null
  }
  stopHeartbeat()
  if (state.ws) {
    state.ws.close()
    state.ws = null
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

  // 组件卸载时自动清理
  onUnmounted(() => {
    // 不断开全局连接，只清理当前组件的处理器
  })

  // 获取任务进度
  const getJobProgress = (jobId: string): JobProgress | undefined => {
    return state.jobProgress.get(jobId)
  }

  // 获取待处理操作
  const getPendingAction = (jobId: string): NeedActionInfo | undefined => {
    return state.pendingActions.get(jobId)
  }

  // 消费历史记录更新
  const consumeHistoryUpdates = (): HistoryUpdate[] => {
    const updates = [...(state.historyUpdates.value ?? [])]
    state.historyUpdates.value = []
    return updates
  }

  // 清空历史记录更新
  const clearHistoryUpdates = () => {
    if (state.historyUpdates.value) {
      state.historyUpdates.value = []
    }
  }

  return {
    // 状态
    isConnected: state.isConnected,
    clientId: computed(() => state.clientId),
    jobProgress: state.jobProgress,
    pendingActions: state.pendingActions,
    historyUpdates: state.historyUpdates,

    // 方法
    connect,
    disconnect,
    subscribe,
    unsubscribe,
    sendUserAction,
    send,

    // 处理器管理
    registerHandler,
    unregisterHandler,

    // 辅助方法
    getJobProgress,
    getPendingAction,
    consumeHistoryUpdates,
    clearHistoryUpdates,
  }
}
