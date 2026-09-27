/**
 * scrape 域常量（收敛原 5 份 LinkMode 映射）
 *
 * 创建任务的**两条流程**（ManualJobCreateModal / TaskWizard）共用同一份
 * 整理模式选项（硬/移/复/软）与同一默认值（LinkMode.MOVE，与后端
 * ManualJobCreate 的默认值一致）。
 */
import type { SelectOption } from 'naive-ui'
import type { ManualJob } from '@/modules/scrape/types'
import { LinkMode } from '@/modules/scrape/types'

/** LinkMode → 中文标签 */
export const LINK_MODE_LABELS: Record<LinkMode, string> = {
  [LinkMode.HARDLINK]: '硬链接',
  [LinkMode.MOVE]: '移动',
  [LinkMode.COPY]: '复制',
  [LinkMode.SYMLINK]: '软链接',
}

/** LinkMode → NTag 类型（ScanPage 表格用） */
export const LINK_MODE_TAG_TYPE: Record<
  LinkMode,
  'success' | 'info' | 'warning' | 'default'
> = {
  [LinkMode.HARDLINK]: 'info',
  [LinkMode.MOVE]: 'warning',
  [LinkMode.COPY]: 'success',
  [LinkMode.SYMLINK]: 'default',
}

/** 整理模式选项（两条创建流程共用） */
export const LINK_MODE_OPTIONS_BASIC: SelectOption[] = [
  { label: '硬链接', value: LinkMode.HARDLINK },
  { label: '移动', value: LinkMode.MOVE },
  { label: '复制', value: LinkMode.COPY },
  { label: '软链接', value: LinkMode.SYMLINK },
]

/** organize_mode（全局配置）→ LinkMode 反向映射（配置复用回填用） */
export const ORGANIZE_MODE_TO_LINK_MODE: Record<string, LinkMode> = {
  copy: LinkMode.COPY,
  move: LinkMode.MOVE,
  hardlink: LinkMode.HARDLINK,
  symlink: LinkMode.SYMLINK,
}

/** 状态筛选选项（ScanPage） */
export const JOB_STATUS_OPTIONS: SelectOption[] = [
  { label: '全部', value: 'all' },
  { label: '等待中', value: 'pending' },
  { label: '运行中', value: 'running' },
  { label: '成功', value: 'success' },
  { label: '失败', value: 'failed' },
  { label: '已取消', value: 'cancelled' },
]

/** ManualJobStatus → StatusBadge 契约（桌面表格与移动卡片共用）
 *
 * 唯一的状态映射表：原先 JOB_STATUS_LABELS + JOB_STATUS_TAG_TYPE（给 NTag 用）
 * 与 JOB_STATUS_BADGE（给 StatusBadge 用）是两份同义表，改文案要同步两处。 */
export const JOB_STATUS_BADGE: Record<
  string,
  { status: 'success' | 'error' | 'warning' | 'info' | 'pending' | 'default'; text: string }
> = {
  pending: { status: 'pending', text: '等待中' },
  running: { status: 'info', text: '运行中' },
  success: { status: 'success', text: '成功' },
  failed: { status: 'error', text: '失败' },
  cancelled: { status: 'warning', text: '已取消' },
}

/** 未完成的手动任务或其刮削子任务数量。 */
export const activeChildCount = (job: ManualJob): number =>
  job.child_pending_count + job.child_running_count + job.child_pending_action_count

/** 有活跃子任务时优先显示真实的处理中状态。 */
export const getJobStatusBadge = (job: ManualJob) => {
  if (activeChildCount(job) > 0) {
    return { status: 'info' as const, text: '处理中' }
  }
  return JOB_STATUS_BADGE[job.status] ?? { status: 'default' as const, text: job.status }
}
