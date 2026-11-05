import { ref } from 'vue'
import { configApi } from '@/shared/api/config'
import { useSettingsForm } from '@/modules/settings/hooks/useSettingsForm'

/**
 * 系统性能配置（SystemSettings 性能设置区）
 *
 * 4 个数值项的加载与保存；错误处理交给 useSettingsForm：
 * 加载失败给 loadError（卡片里显示原因 + 重试），保存失败给 saveError（错误条 + 保留输入）。
 */
export function useSystemConfig() {
  const scrapeThreads = ref(4)
  const taskTimeout = ref(300)
  const retryCount = ref(3)
  const concurrentDownloads = ref(3)

  const load = async () => {
    const config = await configApi.getSystemConfig()
    scrapeThreads.value = config.scrape_threads
    taskTimeout.value = config.task_timeout
    retryCount.value = config.retry_count
    concurrentDownloads.value = config.concurrent_downloads
  }

  const save = async () => {
    await configApi.saveSystemConfig({
      scrape_threads: scrapeThreads.value,
      task_timeout: taskTimeout.value,
      retry_count: retryCount.value,
      concurrent_downloads: concurrentDownloads.value,
    })
  }

  const { loading, saving, loadError, saveError, reload, submit } = useSettingsForm({
    load,
    save,
    successText: '系统配置已保存',
  })

  return {
    loading,
    saving,
    scrapeThreads,
    taskTimeout,
    retryCount,
    concurrentDownloads,
    loadError,
    saveError,
    reload,
    submit,
  }
}
