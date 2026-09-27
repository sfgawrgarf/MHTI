/**
 * WebSocket 节流工具（useWebSocket 拆分，纯逻辑）
 *
 * 从 useWebSocket 原样搬移的两种节流：
 * - 进度更新（200ms 窗口 + rAF 批量写入，Map 按 job_id 取最新）
 * - 处理器通知（100ms 窗口 + rAF 批量，Array 去重保留每个 job_id 的最新消息）
 *
 * 目标状态写入经回调注入，节流器自身不持有任何全局状态；
 * 与 useWebSocket 的单例语义（state 挂 window）无关。
 */
import type { JobProgress, WSMessage } from './wsTypes'

// 进度更新节流配置
const PROGRESS_THROTTLE_MS = 200

// Handler 节流配置
const HANDLER_THROTTLE_MS = 100

/**
 * 创建进度更新节流器
 * @param apply 实际写入目标状态的回调（原 state.jobProgress.set）
 */
export function createProgressThrottle(
  apply: (jobId: string, progress: JobProgress) => void,
) {
  const progressBuffer: Map<string, JobProgress> = new Map()
  let rafId: number | null = null
  let lastFlushTime = 0

  /**
   * 批量刷新进度更新
   */
  function flushProgressUpdates(): void {
    if (progressBuffer.size === 0) {
      rafId = null
      return
    }

    progressBuffer.forEach((progress, jobId) => {
      apply(jobId, progress)
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

  /** 丢弃已进入终态任务的缓冲进度，避免取消后旧进度回写。 */
  function discardProgress(jobId: string): void {
    progressBuffer.delete(jobId)
  }

  return { throttledProgressUpdate, discardProgress }
}

/**
 * 创建处理器通知节流器
 * @param notify 实际通知回调（原 notifyHandlers）
 */
export function createHandlerThrottle(notify: (msg: WSMessage) => void) {
  let handlerBuffer: WSMessage[] = []
  let handlerRafId: number | null = null
  let handlerLastFlushTime = 0

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
      notify(msg)
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

  /** 丢弃终态消息之前排队的同一任务进度通知。 */
  function discardHandlerNotifications(jobId: string): void {
    handlerBuffer = handlerBuffer.filter((msg) => msg.job_id !== jobId)
  }

  return { scheduleHandlerNotification, discardHandlerNotifications }
}
