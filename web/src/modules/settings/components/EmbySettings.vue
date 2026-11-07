<script setup lang="ts">
/**
 * Emby 媒体库集成
 *
 * 版式改由 SettingsSection + SettingsField + SettingsGroup；状态类信息统一改文字/徽章：
 * - API 密钥状态、连接结果从彩色 NTag 胶囊改成「状态 + 说明」一行（连接结果是验证
 *   结论，靠语义色文字表达即可，铺底色只是重复强调）；
 * - 蓝底 NAlert 的「在哪创建密钥」改成常驻 hint；
 * - 媒体库勾选从 NSpace 改为响应式 grid，库多时不会挤成一行。
 */
import { onMounted, ref, watch } from 'vue'
import { NButton, NCheckbox, NCheckboxGroup, NIcon, NInput, NInputNumber, NSwitch } from 'naive-ui'
import { CheckmarkCircleOutline, CloseCircleOutline } from '@vicons/ionicons5'
import { embyApi } from '@/modules/settings/api'
import type { EmbyConfig, EmbyLibrary } from '@/modules/settings/types'
import SettingsSection from '@/modules/settings/components/SettingsSection.vue'
import SettingsField from '@/modules/settings/components/SettingsField.vue'
import SettingsGroup from '@/modules/settings/components/SettingsGroup.vue'
import StatusBadge from '@/shared/components/business/StatusBadge.vue'
import { useSettingsForm } from '@/modules/settings/hooks/useSettingsForm'

const enabled = ref(false)
const serverUrl = ref('')
const apiKey = ref('')
const userId = ref('')
const libraryIds = ref<string[]>([])
const checkBeforeScrape = ref(true)
const timeout = ref(10)

const hasApiKey = ref(false)
const savedServerUrl = ref('')
const connectionStatus = ref<'unknown' | 'success' | 'failed'>('unknown')
const serverName = ref('')
const serverVersion = ref('')
const libraries = ref<EmbyLibrary[]>([])
const latencyMs = ref<number | null>(null)
const testing = ref(false)

/** 已保存过密钥时，测试连接用占位符让后端取存档里的密钥 */
const API_KEY_SAVED = '__USE_SAVED__'

const loadLibraries = async () => {
  try {
    const result = await embyApi.testConnection({
      enabled: enabled.value,
      server_url: serverUrl.value,
      api_key: API_KEY_SAVED,
      user_id: userId.value,
      library_ids: [],
      check_before_scrape: checkBeforeScrape.value,
      timeout: timeout.value,
    })
    if (result.success) {
      libraries.value = result.libraries
      serverName.value = result.server_name || ''
      serverVersion.value = result.server_version || ''
      connectionStatus.value = 'success'
      latencyMs.value = result.latency_ms
    }
  } catch (error) {
    // 媒体库只是可选项，拉不到就不显示勾选区
    console.error('加载媒体库失败:', error)
  }
}

const load = async () => {
  const config: EmbyConfig = await embyApi.getConfig()
  enabled.value = config.enabled
  serverUrl.value = config.server_url
  savedServerUrl.value = config.server_url
  hasApiKey.value = config.has_api_key
  userId.value = config.user_id
  libraryIds.value = config.library_ids
  checkBeforeScrape.value = config.check_before_scrape
  timeout.value = config.timeout
  if (config.has_api_key && config.server_url) await loadLibraries()
}

const save = async () => {
  await embyApi.saveConfig({
    enabled: enabled.value,
    server_url: serverUrl.value,
    api_key: apiKey.value || '',
    user_id: userId.value,
    library_ids: libraryIds.value,
    check_before_scrape: checkBeforeScrape.value,
    timeout: timeout.value,
  })
  savedServerUrl.value = serverUrl.value
  if (apiKey.value) {
    hasApiKey.value = true
    apiKey.value = ''
  }
}

const { loading, saving, loadError, saveError, reload, submit, warn } = useSettingsForm({
  load,
  save,
  successText: 'Emby 配置已保存',
})

const validate = () => {
  if (!enabled.value) return true
  if (!serverUrl.value) {
    warn('请输入服务器地址')
    return false
  }
  if (!apiKey.value && !hasApiKey.value) {
    warn('请输入 API 密钥')
    return false
  }
  if (!apiKey.value && hasApiKey.value && serverUrl.value.replace(/\/+$/, '') !== savedServerUrl.value.replace(/\/+$/, '')) {
    warn('服务器地址已变更，请重新输入 API 密钥')
    return false
  }
  return true
}

const handleSave = () => {
  if (validate()) submit()
}

const testConnection = async () => {
  if (!validate()) return
  testing.value = true
  connectionStatus.value = 'unknown'
  try {
    const result = await embyApi.testConnection({
      enabled: enabled.value,
      server_url: serverUrl.value,
      api_key: apiKey.value || API_KEY_SAVED,
      user_id: userId.value,
      library_ids: libraryIds.value,
      check_before_scrape: checkBeforeScrape.value,
      timeout: timeout.value,
    })
    if (result.success) {
      connectionStatus.value = 'success'
      serverName.value = result.server_name || ''
      serverVersion.value = result.server_version || ''
      libraries.value = result.libraries
      latencyMs.value = result.latency_ms
    } else {
      connectionStatus.value = 'failed'
    }
  } catch (error) {
    connectionStatus.value = 'failed'
    console.error(error)
  } finally {
    testing.value = false
  }
}

onMounted(reload)
watch(serverUrl, value => {
  if (value !== savedServerUrl.value) {
    libraries.value = []
    libraryIds.value = []
    connectionStatus.value = 'unknown'
  }
})
</script>

<template>
  <SettingsSection
    title="Emby"
    description="刮削前检查媒体库里是否已有同一集，避免重复入库"
    :loading="loading"
    :error="loadError"
    :action-error="saveError"
    @retry="reload"
  >
    <SettingsField
      label="启用冲突检查"
      hint="刮削前查一次 Emby，发现同一集已存在则转入人工处理"
      width="sm"
    >
      <NSwitch v-model:value="enabled" />
    </SettingsField>

    <template v-if="enabled">
      <SettingsGroup title="连接">
        <SettingsField label="服务器地址" hint="含协议与端口，例如 http://192.168.1.100:8096" width="lg">
          <NInput v-model:value="serverUrl" placeholder="http://192.168.1.100:8096" />
        </SettingsField>

        <SettingsField
          label="API 密钥"
          hint="在 Emby 控制台 → 高级 → API 密钥 中创建"
          width="lg"
        >
          <StatusBadge
            :status="hasApiKey ? 'success' : 'default'"
            :text="hasApiKey ? '已配置' : '未配置'"
          />
          <NInput
            v-model:value="apiKey"
            type="password"
            show-password-on="click"
            :placeholder="hasApiKey ? '输入新密钥以更新' : '输入 API 密钥'"
          />
        </SettingsField>

        <SettingsField label="用户 ID" hint="留空使用默认用户" width="lg">
          <NInput v-model:value="userId" placeholder="留空使用默认用户" />
        </SettingsField>

        <SettingsField label="请求超时" hint="单位秒，网络慢时可调大" width="sm">
          <NInputNumber v-model:value="timeout" :min="5" :max="60" />
        </SettingsField>

        <SettingsField
          label="刮削前检查"
          hint="在刮削流程中检查冲突（关闭后仅在手动任务里检查）"
          width="sm"
        >
          <NSwitch v-model:value="checkBeforeScrape" />
        </SettingsField>

        <div class="test-row">
          <NButton :loading="testing" @click="testConnection">测试连接</NButton>
          <span v-if="connectionStatus === 'success'" class="test-result is-ok">
            <NIcon :component="CheckmarkCircleOutline" :size="16" />
            已连接 {{ serverName }} v{{ serverVersion }}
            <template v-if="latencyMs !== null">（{{ latencyMs }}ms）</template>
          </span>
          <span v-else-if="connectionStatus === 'failed'" class="test-result is-bad">
            <NIcon :component="CloseCircleOutline" :size="16" />
            连接失败
          </span>
        </div>
      </SettingsGroup>

      <SettingsGroup
        v-if="libraries.length > 0"
        title="检查的媒体库"
        hint="不选择则检查所有媒体库"
      >
        <NCheckboxGroup v-model:value="libraryIds">
          <div class="library-grid">
            <NCheckbox
              v-for="lib in libraries"
              :key="lib.id"
              :value="lib.id"
              :label="`${lib.name} (${lib.item_count})`"
            />
          </div>
        </NCheckboxGroup>
      </SettingsGroup>
    </template>

    <template #actions>
      <NButton type="primary" :loading="saving" @click="handleSave">保存配置</NButton>
    </template>
  </SettingsSection>
</template>

<style scoped>
.test-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--space-3);
}

.test-result {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  font-size: var(--text-sm);
}

.test-result.is-ok {
  color: var(--success-500);
}

.test-result.is-bad {
  color: var(--danger-500);
}

.library-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-3) var(--space-4);
}

@media (max-width: 767px) {
  .library-grid {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
