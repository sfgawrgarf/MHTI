/**
 * settings 域常量
 */
import type { SelectOption } from 'naive-ui'

/** 日志级别下拉选项（'all' 为"不限"哨兵值，消费方按需过滤） */
export const LOG_LEVEL_OPTIONS: SelectOption[] = [
  { label: '全部', value: 'all' },
  { label: 'DEBUG', value: 'DEBUG' },
  { label: 'INFO', value: 'INFO' },
  { label: 'WARNING', value: 'WARNING' },
  { label: 'ERROR', value: 'ERROR' },
  { label: 'CRITICAL', value: 'CRITICAL' },
]

/** 设置分组 key */
export type SectionKey =
  | 'organize'
  | 'watcher'
  | 'download'
  | 'naming'
  | 'network'
  | 'nfo'
  | 'ai'
  | 'cloud115'
  | 'emby'
  | 'system'
  | 'logs'

export interface SettingsSectionMeta {
  key: SectionKey
  label: string
  /** 一句话说明这一组管什么（内容区 h2 下方） */
  description: string
  /** 分组标题：无标题的项直接跟随上一组 */
  group?: string
  /** 宽面板：内含表格（日志），内容区放宽到 1120px */
  wide?: boolean
}

/** 分组顺序与分组名按"日常改动频率"排：整理/监控最常用，日志最靠后 */
export const SETTINGS_SECTIONS: SettingsSectionMeta[] = [
  { key: 'organize', label: '整理', description: '刮削完成后文件的去向、命名与过滤规则', group: '刮削' },
  { key: 'watcher', label: '目录监控', description: '监控目录的扫描方式与入库时机' },
  { key: 'download', label: '下载', description: '海报、剧照与元数据的下载范围' },
  { key: 'naming', label: '命名与语言', description: '元数据语言、文件名模板与重命名规则' },
  { key: 'nfo', label: 'NFO 元数据', description: '生成的 NFO 字段与图片类型', group: '集成' },
  { key: 'ai', label: 'AI 识别', description: '配置 AI 辅助识别与媒体版本策略' },
  { key: 'network', label: '网络代理', description: '刮削与下载请求是否走代理' },
  { key: 'cloud115', label: '115 网盘', description: '115 登录状态与在线处理开关' },
  { key: 'emby', label: 'Emby', description: '服务器地址、API Key 与入库校验', group: '系统' },
  { key: 'system', label: '系统', description: 'TMDB 认证与并发性能参数' },
  { key: 'logs', label: '日志', description: '日志级别、保留策略与查询', wide: true },
]

/** 按 group 字段把分组切成「有标题的组 → 组内项」（导航渲染用） */
export function groupSettingsSections(
  sections: SettingsSectionMeta[] = SETTINGS_SECTIONS,
): { title?: string; items: SettingsSectionMeta[] }[] {
  const result: { title?: string; items: SettingsSectionMeta[] }[] = []
  for (const section of sections) {
    if (section.group || result.length === 0) {
      result.push({ title: section.group, items: [section] })
    } else {
      result[result.length - 1]!.items.push(section)
    }
  }
  return result
}
