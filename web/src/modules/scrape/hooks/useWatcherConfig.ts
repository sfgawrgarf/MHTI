import { ref, computed } from 'vue'
import { watcherApi } from '@/shared/api/watcher'
import { configApi } from '@/shared/api/config'
import type { OrganizeConfig, WatchedFolder } from '@/shared/types/common'

/**
 * 监控目录 + 全局整理配置（ManualJobCreateModal / TaskWizard 共用）
 *
 * 原两处的 loadWatchedFolders / loadGlobalConfig 逐字相同（含错误文案），
 * 收敛于此；configReuseOptions 供两条创建流程共用（此处计算无副作用）。
 */
export function useWatcherConfig() {
  const watchedFolders = ref<WatchedFolder[]>([])
  const globalOrganizeConfig = ref<OrganizeConfig | null>(null)

  /** 加载监控目录列表 */
  const loadWatchedFolders = async () => {
    try {
      const response = await watcherApi.listFolders()
      watchedFolders.value = response.folders
    } catch (error) {
      console.error('加载监控目录失败:', error)
    }
  }

  /** 加载全局整理配置 */
  const loadGlobalConfig = async () => {
    try {
      globalOrganizeConfig.value = await configApi.getOrganizeConfig()
    } catch (error) {
      console.error('加载全局配置失败:', error)
    }
  }

  /** 配置 ID 是不透明字符串，空串表示不复用。 */
  const configReuseOptions = computed(() => {
    const options: Array<{ label: string; value: string }> = [{ label: '不复用', value: '' }]
    watchedFolders.value.forEach((folder) => {
      options.push({ label: folder.path, value: folder.id })
    })
    return options
  })

  return {
    watchedFolders,
    globalOrganizeConfig,
    configReuseOptions,
    loadWatchedFolders,
    loadGlobalConfig,
  }
}
