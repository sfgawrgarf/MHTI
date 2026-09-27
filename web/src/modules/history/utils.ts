/**
 * history 域展示工具（HistoryPage / HistoryRecordCard / HistoryTable / 详情页共用）
 */
import type { HistoryRecord, TaskStatus } from '@/modules/history/types'

/** StatusBadge 支持的语义色（与 StatusBadge.vue 的 BadgeStatus 一致） */
export type BadgeTone = 'success' | 'warning' | 'error' | 'info' | 'pending' | 'default'

/** 格式化时间（zh-CN 本地字符串） */
export const formatTime = (time: string) => new Date(time).toLocaleString('zh-CN')

/** 格式化耗时（秒 → 1.2s / 1.2m） */
export const formatDuration = (seconds: number) => {
  if (seconds < 60) return `${seconds.toFixed(1)}s`
  return `${(seconds / 60).toFixed(1)}m`
}

/**
 * 展示用耗时（秒）。
 *
 * running 记录的 duration_seconds 一直是 0（后端只在跑完才写），所以要用本次执行
 * 的 started_at 现场推算，否则列表与详情页的耗时直到任务结束前都是「0.0s」。
 * 其余状态直接用后端写的最终值（重刮/重新整理后端也会写回）。
 * 旧数据没有 started_at 时退回 duration_seconds，不会显示错值。
 */
export const resolveDurationSeconds = (record: HistoryRecord, nowMs: number): number => {
  if (record.status === 'running' && record.started_at) {
    const startedMs = new Date(record.started_at).getTime()
    if (Number.isFinite(startedMs)) {
      return Math.max(0, (nowMs - startedMs) / 1000)
    }
  }
  return record.duration_seconds
}

/** 从路径提取目录（处理 "源路径 => 目标路径" 格式，显示源文件夹完整路径） */
export const getFolder = (path: string) => {
  // 如果包含 =>，取源路径
  if (path.includes(' => ')) {
    path = path.split(' => ')[0] ?? path
  }
  const parts = path.split(/[/\\]/)
  parts.pop() // 移除文件名
  return parts.join('/') || '/'
}

/** 触发方式（基于 source 字段）：只作元数据标注，不承载颜色语义 */
export const getTriggerLabel = (record: HistoryRecord) =>
  record.source === 'watcher' ? '监控' : '手动'

/**
 * 超时停在哪个节点（列表 / 移动卡片共用）
 *
 * 仅超时记录且后端写回了节点时有值（旧数据可能只有超时状态没有节点）。
 * 文案短是刻意：列表列宽只有 96px，详情页的完整机制说明在详情页。
 */
export const getTimeoutStopLabel = (record: HistoryRecord) => {
  if (record.status !== 'timeout' || !record.timeout_step) return null
  return `停在「${record.timeout_step}」`
}

/** 重试所需的匹配信息（三件套齐全才算可用） */
export interface RetryMatch {
  tmdbId: number
  season: number
  episode: number
}

/**
 * 从记录里取出「上次已确定」的匹配（重试弹窗预填 / 批量重试共用）
 *
 * 两个来源：本轮起后的记录直接读列（tmdb_id/season_number/episode_number）；
 * 旧记录只在冲突分支把匹配写进过 conflict_data（字段名不统一：season/episode
 * 与 parsed_season/parsed_episode 都有），两侧都取不到就返回 null —— 调用方
 * 回退到「搜索剧集」，而不是拿猜测値去刮削。
 */
export const getRetryMatch = (record: HistoryRecord): RetryMatch | null => {
  // 列表记录没有 conflict_data（只有详情接口返回），所以走断言读：有则当兼底用
  const detail = record as { conflict_data?: Record<string, unknown> | null }
  const data = detail.conflict_data ?? {}
  const tmdbId = record.tmdb_id ?? (data.tmdb_id as number | undefined)
  const season = record.season_number ?? (data.season as number | undefined) ?? (data.parsed_season as number | undefined)
  const episode =
    record.episode_number ?? (data.episode as number | undefined) ?? (data.parsed_episode as number | undefined)
  if (!tmdbId || season == null || episode == null) return null
  return { tmdbId, season, episode }
}

/**
 * 任务状态 → 徽章状态 + 文案（StatusBadge 用）
 *
 * 唯一的状态映射表：原先 statusTag（给 NTag 用）与 getStatusBadge 是两份
 * 语义相同、取值空间不同的表，任何一处改文案都要同步两遍。
 */
export const getStatusBadge = (
  status: TaskStatus,
): { status: BadgeTone; text: string } => {
  const map: Record<TaskStatus, { status: BadgeTone; text: string }> = {
    success: { status: 'success', text: '成功' },
    failed: { status: 'error', text: '失败' },
    timeout: { status: 'warning', text: '超时' },
    cancelled: { status: 'warning', text: '取消' },
    skipped: { status: 'default', text: '跳过' },
    deleted: { status: 'default', text: '已删除' },
    replaced: { status: 'default', text: '已替代' },
    pending_action: { status: 'pending', text: '待处理' },
    running: { status: 'info', text: '处理中' },
  }
  return map[status] || { status: 'default', text: status }
}

/**
 * 季集号：任一项存在即渲染单侧，避免出现 S00E00
 *
 * 原先 HistoryTable / HistoryRecordCard / HomeRecentTasks 各写一份，表格那份空值返回
 * '—'、卡片那份返回 null，改口径要改三处；这里统一返回 null，由调用方决定占位符。
 */
export const episodeLabel = (record: HistoryRecord): string | null => {
  const parts: string[] = []
  if (record.season_number != null) parts.push(`S${String(record.season_number).padStart(2, '0')}`)
  if (record.episode_number != null) parts.push(`E${String(record.episode_number).padStart(2, '0')}`)
  return parts.join('') || null
}

/** 格式化耗时（详情页口径：1.2秒 / 1.2分钟） */
export const formatDurationCn = (seconds: number) => {
  if (seconds < 60) return `${seconds.toFixed(1)}秒`
  return `${(seconds / 60).toFixed(1)}分钟`
}
