import type { StorageProvider } from '@/api/types'

export interface BreadcrumbBrowseTarget {
  provider: StorageProvider
  fileId: string | null
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
