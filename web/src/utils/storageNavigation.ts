import type { StorageLocator, StorageProvider, WatchedFolder } from '@/api/types'

export interface BreadcrumbBrowseTarget {
  provider: StorageProvider
  fileId: string | null
}

/** Match only the virtual 115 root and its descendants. */
export function isP115VirtualPath(path: string): boolean {
  const normalized = path.trim().replace(/\/+$/, '')
  return normalized === '/115网盘' || normalized.startsWith('/115网盘/')
}

/** Resolve a breadcrumb destination without reusing a descendant provider ID. */
export function resolveBreadcrumbBrowseTarget(
  path: string,
  currentProvider: StorageProvider,
): BreadcrumbBrowseTarget {
  if (path === '') {
    return { provider: 'local', fileId: null }
  }
  if (path === '/115网盘') {
    return { provider: '115', fileId: '0' }
  }
  return { provider: currentProvider, fileId: null }
}

/** Preserve provider identity when a watched folder is used as a task source. */
export function locatorForWatchedFolder(folder: WatchedFolder): StorageLocator {
  const provider: StorageProvider =
    folder.provider === '115' || isP115VirtualPath(folder.path) ? '115' : 'local'
  return {
    provider,
    path: folder.path,
    file_id: provider === '115' ? folder.file_id ?? null : undefined,
    is_dir: true,
  }
}

/** Infer the provider for a configured directory that has no persisted file ID. */
export function locatorForConfiguredPath(path: string): StorageLocator {
  const normalizedPath = path.trim()
  const isP115 = isP115VirtualPath(normalizedPath)
  return {
    provider: isP115 ? '115' : 'local',
    path: normalizedPath,
    file_id: isP115 ? null : undefined,
    is_dir: true,
  }
}
