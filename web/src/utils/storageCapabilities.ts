import { LinkMode } from '@/api/types'
import type { StorageLocator } from '@/api/types'

export interface StorageSourceSelection {
  path: string
  locator: StorageLocator | null
}

export interface StorageSelection {
  sources: StorageSourceSelection[]
  targetPath: string
  targetLocator: StorageLocator | null
  metadataPath: string
  metadataLocator: StorageLocator | null
  allowLocalOutput: boolean
  linkMode: LinkMode
}

const isP115Path = (path: string) => {
  const normalized = path.replace(/\/+$/, '')
  return normalized === '/115网盘' || normalized.startsWith('/115网盘/')
}

export const isP115Selection = (path: string, locator: StorageLocator | null) =>
  locator?.provider === '115' || (!locator && isP115Path(path))

export const isP115OrganizeMode = (mode: LinkMode) =>
  mode === LinkMode.COPY || mode === LinkMode.MOVE

/** Return the first reason why a storage selection cannot be executed. */
export function getStorageSelectionError(selection: StorageSelection): string | null {
  const metadataIsP115 = isP115Selection(
    selection.metadataPath,
    selection.metadataLocator,
  )
  if (selection.metadataPath && metadataIsP115) {
    return '元数据目录仅支持本地媒体目录'
  }

  const hasP115Source = selection.sources.some(source =>
    isP115Selection(source.path, source.locator),
  )
  const hasLocalSource = selection.sources.some(source =>
    !isP115Selection(source.path, source.locator),
  )
  const targetIsP115 = isP115Selection(
    selection.targetPath,
    selection.targetLocator,
  )

  if (hasLocalSource && targetIsP115) {
    return '暂不支持将本地文件输出到 115 网盘'
  }
  if (
    hasP115Source &&
    !isP115OrganizeMode(selection.linkMode)
  ) {
    return '115 源文件仅支持复制或移动整理模式'
  }
  if (hasP115Source && !targetIsP115 && !selection.allowLocalOutput) {
    return '115 文件输出到本地前必须开启“允许下载到本地”'
  }
  return null
}
