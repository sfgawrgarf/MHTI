<script setup lang="ts">
/**
 * 代理服务器配置
 *
 * 版式改由 SettingsSection + SettingsField；两处设计调整：
 * 1. 「已配置认证信息」「测试结果」从彩色 NTag 胶囊改成状态行 + 结果行：
 *    测试结果是验证结论，用 success/danger 的文字色表达比胶囊底色更轻；
 * 2. 字段宽度用档位（sm/md），不再靠内联 style。
 * 保存/测试失败的错误信息除了 toast 也落在分组顶部的错误条（可回看，不会一闪而过）。
 */
import { computed, onMounted, ref } from 'vue'
import { NButton, NCheckbox, NIcon, NInput, NInputNumber, NRadio, NRadioGroup } from 'naive-ui'
import { CheckmarkCircleOutline, CloseCircleOutline } from '@vicons/ionicons5'
import { configApi } from '@/shared/api/config'
import type { ProxyType } from '@/shared/types/common'
import SettingsSection from '@/modules/settings/components/SettingsSection.vue'
import SettingsField from '@/modules/settings/components/SettingsField.vue'
import { useSettingsForm } from '@/modules/settings/hooks/useSettingsForm'

const proxyType = ref<ProxyType>('none')
const host = ref('')
const port = ref<number | null>(null)
const username = ref('')
const password = ref('')
const hasAuth = ref(false)
const clearAuth = ref(false)
const testResult = ref<{ success: boolean; message: string; latency?: number } | null>(null)
const testing = ref(false)

const isConfigured = computed(() => proxyType.value !== 'none' && !!host.value && !!port.value)

const load = async () => {
  const config = await configApi.getProxyConfig()
  proxyType.value = config.type
  host.value = config.host
  port.value = config.port || null
  hasAuth.value = config.has_auth
  clearAuth.value = false
  // 不加载密码，保持空
}

const save = async () => {
  await configApi.saveProxyConfig({
    type: proxyType.value,
    host: host.value,
    port: port.value || 0,
    username: username.value || null,
    password: password.value || null,
    clear_auth: clearAuth.value,
  })
  testResult.value = null
  username.value = ''
  password.value = ''
  await load()
}

const { loading, saving, loadError, saveError, reload, submit, warn } = useSettingsForm({
  load,
  save,
  successText: '代理配置已保存',
})

/** 校验不通过时的临时提示（字段级校验见 worklog 未修问题） */
const validate = () => {
  if (proxyType.value === 'none') return true
  if (!host.value) {
    warn('请输入代理地址')
    return false
  }
  if (!port.value) {
    warn('请输入代理端口')
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
  testResult.value = null
  try {
    const result = await configApi.testProxy({
      type: proxyType.value,
      host: host.value,
      port: port.value || 0,
      username: username.value || null,
      password: password.value || null,
    clear_auth: clearAuth.value,
    })
    testResult.value = {
      success: result.success,
      message: result.message,
      latency: result.latency_ms ?? undefined,
    }
  } catch (error) {
    testResult.value = { success: false, message: '测试失败' }
    console.error(error)
  } finally {
    testing.value = false
  }
}

const clearConfig = async () => {
  try {
    await configApi.deleteProxyConfig()
    proxyType.value = 'none'
    host.value = ''
    port.value = null
    username.value = ''
    password.value = ''
    hasAuth.value = false
    testResult.value = null
    await reload()
  } catch (error) {
    console.error(error)
  }
}

onMounted(reload)
</script>

<template>
  <SettingsSection
    title="网络代理"
    description="TMDB 与图片下载走代理，适合本机直连超时的网络环境"
    :loading="loading"
    :error="loadError"
    :action-error="saveError"
    @retry="reload"
  >
    <SettingsField label="代理类型" width="full">
      <NRadioGroup v-model:value="proxyType">
        <div class="type-list">
          <NRadio value="none">不使用代理</NRadio>
          <NRadio value="http">HTTP 代理</NRadio>
          <NRadio value="socks5">SOCKS5 代理</NRadio>
        </div>
      </NRadioGroup>
    </SettingsField>

    <template v-if="proxyType !== 'none'">
      <div class="field-row">
        <SettingsField label="代理地址" hint="例如 127.0.0.1" width="lg">
          <NInput v-model:value="host" placeholder="127.0.0.1" />
        </SettingsField>
        <SettingsField label="端口" width="sm">
          <NInputNumber v-model:value="port" :min="1" :max="65535" placeholder="7890" />
        </SettingsField>
      </div>

      <div class="field-row">
        <SettingsField label="用户名" hint="无需认证时留空" width="md">
          <NInput v-model:value="username" placeholder="用户名" />
        </SettingsField>
        <SettingsField label="密码" hint="无需认证时留空" width="md">
          <NInput v-model:value="password" type="password" placeholder="密码" show-password-on="click" />
        </SettingsField>
      </div>

      <NCheckbox v-if="hasAuth" v-model:checked="clearAuth">清除已保存的代理认证信息</NCheckbox>
      <p v-if="hasAuth" class="note">已保存过认证信息，留空则沿用原密码</p>
    </template>

    <p v-if="testResult" class="test-result" :class="testResult.success ? 'is-ok' : 'is-bad'">
      <NIcon
        :component="testResult.success ? CheckmarkCircleOutline : CloseCircleOutline"
        :size="16"
      />
      <span>
        {{ testResult.message }}
        <template v-if="testResult.latency"> （{{ testResult.latency }}ms）</template>
      </span>
    </p>

    <template #actions>
      <NButton type="primary" :loading="saving" @click="handleSave">保存配置</NButton>
      <NButton :loading="testing" :disabled="!isConfigured" @click="testConnection">
        测试连接
      </NButton>
      <NButton v-if="isConfigured" @click="clearConfig">清除配置</NButton>
    </template>
  </SettingsSection>
</template>

<style scoped>
.type-list {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-4);
}

/* 地址 + 端口、用户名 + 密码：成对字段同一行，窄屏自动换行 */
.field-row {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-3) var(--space-6);
}

.note {
  margin: 0;
  font-size: var(--text-sm);
  color: var(--text-2);
}

.test-result {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin: 0;
  font-size: var(--text-sm);
}

.test-result.is-ok {
  color: var(--success-500);
}

.test-result.is-bad {
  color: var(--danger-500);
}
</style>
