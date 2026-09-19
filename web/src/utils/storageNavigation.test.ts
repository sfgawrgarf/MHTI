import { describe, expect, it } from 'vitest'

import { resolveBreadcrumbBrowseTarget } from './storageNavigation'

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
})
