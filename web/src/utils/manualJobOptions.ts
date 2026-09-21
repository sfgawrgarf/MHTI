import type { ManualJobAdvancedSettings } from '@/api/types'

export interface WizardTaskOptions {
  downloadPoster: boolean
  downloadBackdrop: boolean
  downloadThumbnail: boolean
  generateNfo: boolean
  processSubtitle: boolean
  overwriteExisting: boolean
}

const defaultAdvancedSettings = (): ManualJobAdvancedSettings => ({
  use_global_organize: true,
  use_global_download: true,
  use_global_naming: true,
  use_global_metadata: true,
  scan_filters_enabled: false,
  metadata_folder: '',
  delete_metadata_on_fail: false,
  overwrite_video: false,
  overwrite_image: false,
  file_size_filter: 100,
  file_ext_whitelist: [],
  file_name_blacklist: [],
  file_sanitize_list: [],
  protect_ext_whitelist: false,
  delete_by_size: false,
  delete_by_ext: false,
  delete_by_name: false,
  extra_ext_whitelist: [],
  download_poster: true,
  download_thumb: true,
  download_fanart: false,
  series_folder_template: '',
  season_folder_template: '',
  episode_file_template: '',
  scrape_title: true,
  scrape_plot: true,
  nfo_enabled: true,
  process_subtitle: true,
})

/** Convert the wizard's quick switches into the persisted task-level options. */
export function buildWizardAdvancedSettings(
  options: WizardTaskOptions,
  current: ManualJobAdvancedSettings | null = null,
): ManualJobAdvancedSettings {
  const settings = current ?? defaultAdvancedSettings()
  return {
    ...settings,
    use_global_organize: options.overwriteExisting ? false : settings.use_global_organize,
    use_global_download: false,
    use_global_metadata: false,
    download_poster: options.downloadPoster,
    download_fanart: options.downloadBackdrop,
    download_thumb: options.downloadThumbnail,
    nfo_enabled: options.generateNfo,
    process_subtitle: options.processSubtitle,
    overwrite_video: options.overwriteExisting,
    overwrite_image: options.overwriteExisting,
  }
}
