import { ref, computed, watch } from 'vue'
import { useMessage, type DataTableRowKey } from 'naive-ui'
import { filesApi } from '@/modules/library/api'
import type { ScannedFile } from '@/modules/library/types'
import type { StorageLocator } from '@/shared/types/common'

/**
 * 文件扫描（FileScanPage 核心）
 *
 * 扫描状态 + 扫描执行 + 客户端分页 + 选中行。分页为纯前端切片
 * （与 FilesPage 的服务端分页不同），逐字保留原实现。
 */
export function useFileScan() {
  const message = useMessage()

  const loading = ref(false)
  const scanPath = ref('')
  const scanLocator = ref<StorageLocator | null>(null)
  const scannedFiles = ref<ScannedFile[]>([])
  const total = ref(0)
  const page = ref(1)
  const pageSize = ref(20)
  const checkedRowKeys = ref<DataTableRowKey[]>([])

  // 分页后的文件列表
  const paginatedFiles = computed(() => {
    const start = (page.value - 1) * pageSize.value
    const end = start + pageSize.value
    return scannedFiles.value.slice(start, end)
  })

  let generation = 0
  watch(scanPath, (path) => {
    generation++
    if (scanLocator.value?.path !== path) scanLocator.value = null
    scannedFiles.value = []
    checkedRowKeys.value = []
    total.value = 0
    loading.value = false
  }, { flush: 'sync' })

  // 扫描目录
  const handleScan = async () => {
    if (!scanPath.value) {
      message.warning('请输入扫描路径')
      return
    }

    const request = ++generation
    loading.value = true
    scannedFiles.value = []
    total.value = 0
    checkedRowKeys.value = []
    page.value = 1

    try {
      const response = await filesApi.scan(scanPath.value, true, scanLocator.value)
      if (generation !== request) return
      scannedFiles.value = response.files
      total.value = response.total_files

      if (response.total_files === 0) {
        message.info('未找到视频文件')
      } else {
        message.success(`找到 ${response.total_files} 个视频文件`)
      }
    } catch (error) {
      if (generation !== request) return
      message.error('扫描失败')
      console.error(error)
    } finally {
      if (generation === request) loading.value = false
    }
  }

  // 接收文件夹选择器的 locator（115 等云端目录）
  const handleFolderLocator = (locator: StorageLocator) => {
    scanLocator.value = locator
  }

  // 分页变化
  const handlePageChange = (p: number) => {
    page.value = p
    checkedRowKeys.value = []
  }

  // 选中行变化
  const handleCheckedRowKeysChange = (keys: DataTableRowKey[]) => {
    checkedRowKeys.value = keys
  }

  return {
    loading,
    scanPath,
    scanLocator,
    scannedFiles,
    total,
    page,
    pageSize,
    checkedRowKeys,
    paginatedFiles,
    handleScan,
    handleFolderLocator,
    handlePageChange,
    handleCheckedRowKeysChange,
  }
}