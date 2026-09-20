import { describe, expect, it } from 'vitest'

import {
  locatorForConfiguredPath,
  locatorForWatchedFolder,
  resolveBreadcrumbBrowseTarget,
} from './storageNavigation'

describe('resolveBreadcrumbBrowseTarget', () => {
  it('returns to the local root from either provider', () => {
    expect(resolveBreadcrumbBrowseTarget('', '115')).toEqual({
      provider: 'local',
      fileId: null,
    })
  })

  it('uses the fixed provider root id for the 115 root', () => {
    expect(resolveBreadcrumbBrowseTarget('/115网盘', '115')).toEqual({
      provider: '115',
      fileId: '0',
    })
  })

  it('resolves a 115 ancestor by path instead of reusing the current directory id', () => {
    expect(resolveBreadcrumbBrowseTarget('/115网盘/剧集', '115')).toEqual({
      provider: '115',
      fileId: null,
    })
  })

  it('keeps a watched 115 folder locator and its file id', () => {
    expect(locatorForWatchedFolder({
      id: 'ab12cd34',
      path: '/115网盘/待整理',
      enabled: true,
      mode: 'compat',
      scan_interval_seconds: 60,
      file_stable_seconds: 30,
      auto_scrape: true,
      output_dir: null,
      provider: '115',
      file_id: 'folder-1',
      last_scan: null,
      created_at: null,
    })).toEqual({
      provider: '115',
      path: '/115网盘/待整理',
      file_id: 'folder-1',
      is_dir: true,
    })
  })

  it('infers a configured 115 path without inventing a file id', () => {
    expect(locatorForConfiguredPath('/115网盘/媒体')).toEqual({
      provider: '115',
      path: '/115网盘/媒体',
      file_id: null,
      is_dir: true,
    })
  })
})
