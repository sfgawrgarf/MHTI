import { describe, expect, it } from 'vitest'

import { LinkMode } from '@/api/types'
import { getStorageSelectionError } from './storageCapabilities'

const baseSelection = {
  sources: [{ path: '/incoming', locator: null }],
  targetPath: '/library',
  targetLocator: null,
  metadataPath: '',
  metadataLocator: null,
  allowLocalOutput: false,
  linkMode: LinkMode.MOVE,
}

describe('getStorageSelectionError', () => {
  it('requires explicit permission before downloading 115 files locally', () => {
    expect(getStorageSelectionError({
      ...baseSelection,
      sources: [{
        path: '/115网盘/待整理',
        locator: { provider: '115', path: '/115网盘/待整理', is_dir: true },
      }],
    })).toContain('允许下载到本地')
  })

  it('allows an explicitly approved 115 to local copy', () => {
    expect(getStorageSelectionError({
      ...baseSelection,
      sources: [{
        path: '/115网盘/待整理',
        locator: { provider: '115', path: '/115网盘/待整理', is_dir: true },
      }],
      allowLocalOutput: true,
      linkMode: LinkMode.COPY,
    })).toBeNull()
  })

  it('rejects move when a 115 file is downloaded to local storage', () => {
    expect(getStorageSelectionError({
      ...baseSelection,
      sources: [{
        path: '/115网盘/待整理',
        locator: { provider: '115', path: '/115网盘/待整理', is_dir: true },
      }],
      allowLocalOutput: true,
      linkMode: LinkMode.MOVE,
    })).toContain('下载到本地仅支持复制')
  })

  it('rejects link modes that depend on a local 115 source file', () => {
    expect(getStorageSelectionError({
      ...baseSelection,
      sources: [{
        path: '/115网盘/待整理',
        locator: { provider: '115', path: '/115网盘/待整理', is_dir: true },
      }],
      targetPath: '/115网盘/媒体库',
      targetLocator: { provider: '115', path: '/115网盘/媒体库', is_dir: true },
      linkMode: LinkMode.HARDLINK,
    })).toContain('复制或移动')
  })

  it('rejects local uploads and cloud metadata directories', () => {
    expect(getStorageSelectionError({
      ...baseSelection,
      targetPath: '/115网盘/媒体库',
      targetLocator: { provider: '115', path: '/115网盘/媒体库', is_dir: true },
    })).toContain('本地文件输出到 115')

    expect(getStorageSelectionError({
      ...baseSelection,
      metadataPath: '/115网盘/元数据',
      metadataLocator: { provider: '115', path: '/115网盘/元数据', is_dir: true },
    })).toContain('元数据目录仅支持本地')
  })
})
