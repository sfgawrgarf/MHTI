<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import {
  NCard,
  NSpace,
  NInput,
  NInputNumber,
  NButton,
  NSelect,
  NSwitch,
  NFormItem,
  NDynamicTags,
  NIcon,
  useMessage,
} from 'naive-ui'
import { FolderOutline } from '@vicons/ionicons5'
import { configApi } from '@/api/config'
import type { OrganizeMode } from '@/api/types'
import FolderBrowserModal from '@/components/scan/FolderBrowserModal.vue'
import { isP115VirtualPath } from '@/utils/storageNavigation'

const message = useMessage()
const loading = ref(false)
const saving = ref(false)

const organizeDir = ref('')
const metadataDir = ref('')
const organizeMode = ref<OrganizeMode>('copy')
const minFileSizeMb = ref(100)
const fileTypeWhitelist = ref<string[]>(['mkv', 'mp4', 'avi', 'wmv', 'ts', 'rmvb'])
const filenameBlacklist = ref<string[]>(['sample', 'trailer'])
const junkPatternFilter = ref<string[]>([])
const autoCleanSource = ref(false)
const metadataDirError = computed(() =>
  metadataDir.value.trim() && isP115VirtualPath(metadataDir.value)
    ? '元数据目录仅支持本地媒体目录'
    : null,
)

// 文件夹浏览器状态
const showOrganizeDirBrowser = ref(false)
const showMetadataDirBrowser = ref(false)

const modeOptions = [
  { label: '复制', value: 'copy' },
  { label: '移动', value: 'move' },
  { label: '硬链接', value: 'hardlink' },
  { label: '软链接', value: 'symlink' },
]

const loadConfig = async () => {
  loading.value = true
  try {
    const config = await configApi.getOrganizeConfig()
    organizeDir.value = config.organize_dir
    metadataDir.value = config.metadata_dir
    organizeMode.value = config.organize_mode
    minFileSizeMb.value = config.min_file_size_mb
    fileTypeWhitelist.value = config.file_type_whitelist
    filenameBlacklist.value = config.filename_blacklist
    junkPatternFilter.value = config.junk_pattern_filter
    autoCleanSource.value = config.auto_clean_source
  } catch (error) {
    console.error(error)
  } finally {
    loading.value = false
  }
}

const saveConfig = async () => {
  if (metadataDirError.value) {
    message.warning(metadataDirError.value)
    return
  }
  saving.value = true
  try {
    await configApi.saveOrganizeConfig({
      organize_dir: organizeDir.value,
      metadata_dir: metadataDir.value,
      organize_mode: organizeMode.value,
      min_file_size_mb: minFileSizeMb.value,
      file_type_whitelist: fileTypeWhitelist.value,
      filename_blacklist: filenameBlacklist.value,
      junk_pattern_filter: junkPatternFilter.value,
      auto_clean_source: autoCleanSource.value,
    })
    message.success('整理配置已保存')
  } catch (error) {
    message.error('保存失败')
    console.error(error)
  } finally {
    saving.value = false
  }
}

// 处理文件夹选择
const handleOrganizeDirConfirm = (path: string) => {
  organizeDir.value = path
}

const handleMetadataDirConfirm = (path: string) => {
  metadataDir.value = path
}

onMounted(loadConfig)
</script>

<template>
  <NCard title="整理配置" size="small">
    <NSpace vertical>
      <NFormItem label="整理目录">
        <div class="path-input">
          <NInput v-model:value="organizeDir" placeholder="整理后的文件存放目录" />
          <NButton @click="showOrganizeDirBrowser = true">
            <template #icon>
              <NIcon :component="FolderOutline" />
            </template>
          </NButton>
        </div>
      </NFormItem>

      <NFormItem
        label="元数据目录"
        :validation-status="metadataDirError ? 'error' : undefined"
        :feedback="metadataDirError || undefined"
      >
        <div class="path-input">
          <NInput v-model:value="metadataDir" placeholder="NFO和图片存放目录（留空则与视频同目录）" />
          <NButton @click="showMetadataDirBrowser = true">
            <template #icon>
              <NIcon :component="FolderOutline" />
            </template>
          </NButton>
        </div>
      </NFormItem>

      <NFormItem label="整理模式">
        <NSelect v-model:value="organizeMode" :options="modeOptions" style="width: 200px" />
      </NFormItem>

      <NFormItem label="文件大小过滤 (MB)">
        <NInputNumber v-model:value="minFileSizeMb" :min="0" :max="10000" style="width: 150px" />
        <span style="margin-left: 8px; color: #999">小于此大小的文件将被忽略</span>
      </NFormItem>

      <NFormItem label="文件类型白名单">
        <NDynamicTags v-model:value="fileTypeWhitelist" />
      </NFormItem>

      <NFormItem label="文件名黑名单">
        <NDynamicTags v-model:value="filenameBlacklist" />
      </NFormItem>

      <NFormItem label="垃圾信息过滤">
        <NDynamicTags v-model:value="junkPatternFilter" />
        <span style="margin-left: 8px; color: #999">支持正则表达式</span>
      </NFormItem>

      <NFormItem label="自动清理源目录">
        <NSwitch v-model:value="autoCleanSource" />
        <span style="margin-left: 8px; color: #999">整理完成后删除源文件</span>
      </NFormItem>

      <NSpace>
        <NButton
          type="primary"
          :loading="saving"
          :disabled="metadataDirError !== null"
          @click="saveConfig"
        >
          保存配置
        </NButton>
      </NSpace>
    </NSpace>
  </NCard>

  <!-- 整理目录选择弹窗 -->
  <FolderBrowserModal
    v-model:show="showOrganizeDirBrowser"
    title="选择整理目录"
    @confirm="handleOrganizeDirConfirm"
  />

  <!-- 元数据目录选择弹窗 -->
  <FolderBrowserModal
    v-model:show="showMetadataDirBrowser"
    title="选择元数据目录"
    :allow-p115="false"
    @confirm="handleMetadataDirConfirm"
  />
</template>

<style scoped>
.path-input {
  display: flex;
  gap: 8px;
  width: 100%;
}

.path-input .n-input {
  flex: 1;
}
</style>
