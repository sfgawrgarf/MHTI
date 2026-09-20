import { describe, expect, it } from 'vitest'

import { buildWizardAdvancedSettings } from './manualJobOptions'

describe('buildWizardAdvancedSettings', () => {
  it('persists every quick option instead of silently using global settings', () => {
    const settings = buildWizardAdvancedSettings({
      downloadPoster: false,
      downloadBackdrop: true,
      downloadThumbnail: false,
      generateNfo: false,
      processSubtitle: false,
      overwriteExisting: true,
    })

    expect(settings).toMatchObject({
      use_global_organize: false,
      use_global_download: false,
      use_global_metadata: false,
      download_poster: false,
      download_fanart: true,
      download_thumb: false,
      nfo_enabled: false,
      process_subtitle: false,
      overwrite_video: true,
      overwrite_image: true,
    })
  })

  it('preserves unrelated advanced settings', () => {
    const original = buildWizardAdvancedSettings({
      downloadPoster: true,
      downloadBackdrop: false,
      downloadThumbnail: true,
      generateNfo: true,
      processSubtitle: true,
      overwriteExisting: false,
    })
    original.file_size_filter = 512
    original.use_global_naming = false

    const settings = buildWizardAdvancedSettings({
      downloadPoster: false,
      downloadBackdrop: false,
      downloadThumbnail: false,
      generateNfo: false,
      processSubtitle: false,
      overwriteExisting: false,
    }, original)

    expect(settings.file_size_filter).toBe(512)
    expect(settings.use_global_naming).toBe(false)
  })
})
