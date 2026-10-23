import { ref, computed } from 'vue'
import { fsApi } from '@/shared/api/fs'
import type {
  BrowseResponse,
  DirectoryEntry,
  StorageLocator,
  StorageProvider,
} from '@/shared/types/common'

/**
 * 文件夹浏览器 composable
 *
 * 从 FolderBrowser / FolderBrowserModal 提取的共同逻辑：
 * 目录加载（含 115 provider 与 file_id 跟踪）/ 进入目录 / 返回上级 /
 * 刷新 / StorageLocator 构造 / provider 标签。
 *
 * 各组件差异保留在组件内：
 * - 打开时的重置与触发时机（watch 由组件持有）
 * - 错误提示方式（经 onError 回调注入）
 * - 单次加载成功后的 UI 同步（经 onLoaded 回调注入，如路径输入框/已选路径）
 */
export interface UseFolderBrowserOptions {
  /** 加载失败回调；不传则静默（与调用方原有行为一致） */
  onError?: (error: unknown) => void
  /** 单次加载成功回调（在内部状态更新后调用） */
  onLoaded?: (response: BrowseResponse) => void
}

export function useFolderBrowser(options: UseFolderBrowserOptions = {}) {
  const loading = ref(false)
  const currentPath = ref('')
  const parentPath = ref<string | null>(null)
  const entries = ref<DirectoryEntry[]>([])
  // 存储提供方跟踪：本地浏览 vs 115 网盘
  const currentProvider = ref<StorageProvider>('local')
  const currentFileId = ref<string | null>(null)
  // 父目录的 file_id（来自后端响应），返回上级时直接使用，无需前端维护栈
  const parentFileId = ref<string | null>(null)

  let loadSequence = 0

  // 加载目录
  const loadDirectory = async (
    path: string = '',
    provider?: StorageProvider,
    fileId?: string | null,
  ) => {
    const sequence = ++loadSequence
    loading.value = true
    const effectiveProvider = provider ?? currentProvider.value
    const effectiveFileId = fileId !== undefined ? fileId : currentFileId.value
    try {
      const pageSize = 100
      const response = await fsApi.browse(path, 1, pageSize, effectiveProvider, effectiveFileId)
      const allEntries = [...response.entries]
      let pageEntries = response.entries
      for (let page = 2; allEntries.length < response.total && pageEntries.length > 0; page++) {
        if (sequence !== loadSequence) return
        const next = await fsApi.browse(path, page, pageSize, effectiveProvider, effectiveFileId)
        pageEntries = next.entries
        allEntries.push(...pageEntries)
      }
      if (sequence !== loadSequence) return
      currentPath.value = response.current_path
      parentPath.value = response.parent_path
      entries.value = allEntries
      currentProvider.value = effectiveProvider
      // 优先用后端返回的 file_id（115 子目录必须），fallback 到请求时的值
      currentFileId.value = response.current_file_id ?? effectiveFileId ?? null
      parentFileId.value = response.parent_file_id ?? null
      options.onLoaded?.({ ...response, entries: allEntries })
    } catch (error: unknown) {
      if (sequence === loadSequence) options.onError?.(error)
    } finally {
      if (sequence === loadSequence) loading.value = false
    }
  }

  // 进入目录
  const enterDirectory = (entry: DirectoryEntry) => {
    if (!entry.is_dir) return
    // 点击虚拟 115 根入口 → 切到 115 provider
    if (entry.is_virtual && entry.provider === '115') {
      loadDirectory(entry.path, '115', entry.file_id ?? '0')
      return
    }
    // 同 provider 内进入子目录：115 用 file_id，本地用 path
    if (currentProvider.value === '115') {
      loadDirectory(entry.path, '115', entry.file_id ?? null)
    } else {
      loadDirectory(entry.path, 'local', null)
    }
  }

  // 返回上级
  const goUp = () => {
    if (parentPath.value === null) {
      // 115 根的上级 → 回到本地根
      if (currentProvider.value === '115') {
        loadDirectory('', 'local', null)
      }
      return
    }
    // 用后端响应里的父目录 file_id（115 必须），本地为 null 走 path
    loadDirectory(parentPath.value, currentProvider.value, parentFileId.value)
  }

  // 刷新当前目录
  const refresh = () => {
    loadDirectory(currentPath.value)
  }

  // 构造当前目录的 StorageLocator
  const buildLocator = (): StorageLocator => {
    if (currentProvider.value === '115') {
      return {
        provider: '115',
        path: currentPath.value,
        file_id: currentFileId.value,
        is_dir: true,
      }
    }
    return {
      provider: 'local',
      path: currentPath.value,
      is_dir: true,
    }
  }

  // 重置为本地根并加载指定路径（打开弹窗时的统一入口）
  const reset = (path: string = '') => {
    currentProvider.value = 'local'
    currentFileId.value = null
    parentFileId.value = null
    loadDirectory(path, 'local', null)
  }

  // 仅目录项
  const directories = computed(() => {
    return entries.value.filter((e) => e.is_dir)
  })

  // 当前 provider 标签
  const providerTag = computed(() => {
    if (currentProvider.value === '115') return { label: '115 网盘', type: 'info' as const }
    return { label: '本地', type: 'default' as const }
  })

  return {
    loading,
    currentPath,
    parentPath,
    entries,
    currentProvider,
    currentFileId,
    parentFileId,
    loadDirectory,
    enterDirectory,
    goUp,
    refresh,
    buildLocator,
    reset,
    directories,
    providerTag,
  }
}
