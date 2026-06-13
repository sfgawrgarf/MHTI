<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import {
  NCard,
  NSpace,
  NInput,
  NInputNumber,
  NButton,
  NTag,
  NRadioGroup,
  NRadio,
  NFormItem,
  useMessage,
} from 'naive-ui'
import { configApi } from '@/api/config'
import type { ProxyType } from '@/api/types'

const message = useMessage()
const loading = ref(false)
const saving = ref(false)
const testing = ref(false)

const proxyType = ref<ProxyType>('none')
const host = ref('')
const port = ref<number | null>(null)
const username = ref('')
const password = ref('')
const hasAuth = ref(false)
const testResult = ref<{ success: boolean; message: string; latency?: number } | null>(null)

const isConfigured = computed(() => proxyType.value !== 'none' && host.value && port.value)

// 加载配置
const loadConfig = async () => {
  loading.value = true
  try {
    const config = await configApi.getProxyConfig()
    proxyType.value = config.type
    host.value = config.host
    port.value = config.port || null
    hasAuth.value = config.has_auth
    // 不加载密码，保持空
  } catch (error) {
    console.error(error)
  } finally {
    loading.value = false
  }
}

// 保存配置
const saveConfig = async () => {
  if (proxyType.value !== 'none') {
    if (!host.value) {
      message.warning('请输入代理地址')
      return
    }
    if (!port.value) {
      message.warning('请输入代理端口')
      return
    }
  }

  saving.value = true
  try {
    await configApi.saveProxyConfig({
      type: proxyType.value,
      host: host.value,
      port: port.value || 0,
      username: username.value || null,
      password: password.value || null,
    })
    message.success('代理配置已保存')
    testResult.value = null
    // 清空密码输入
    username.value = ''
    password.value = ''
    await loadConfig()
  } catch (error) {
    message.error('保存失败')
    console.error(error)
  } finally {
    saving.value = false
  }
}

// 测试连接
const testConnection = async () => {
  testing.value = true
  testResult.value = null
  try {
    const result = await configApi.testProxy({
      type: proxyType.value,
      host: host.value,
      port: port.value || 0,
      username: username.value || null,
      password: password.value || null,
    })
    testResult.value = {
      success: result.success,
      message: result.message,
      latency: result.latency_ms ?? undefined,
    }
    if (result.success) {
      message.success(`连接成功 (${result.latency_ms}ms)`)
    } else {
      message.error(result.message)
    }
  } catch (error) {
    message.error('测试失败')
    console.error(error)
  } finally {
    testing.value = false
  }
}

// 清除配置
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
    message.success('代理配置已清除')
  } catch (error) {
    message.error('清除失败')
    console.error(error)
  }
}

onMounted(loadConfig)
</script>

<template>
  <NCard title="代理服务器配置" size="small">
    <NSpace vertical>
      <!-- 代理类型 -->
      <NFormItem label="代理类型">
        <NRadioGroup v-model:value="proxyType">
          <NRadio value="none">不使用代理</NRadio>
          <NRadio value="http">HTTP 代理</NRadio>
          <NRadio value="socks5">SOCKS5 代理</NRadio>
        </NRadioGroup>
      </NFormItem>

      <template v-if="proxyType !== 'none'">
        <!-- 代理地址 -->
        <NSpace>
          <NFormItem label="代理地址">
            <NInput v-model:value="host" placeholder="127.0.0.1" style="width: 200px" />
          </NFormItem>
          <NFormItem label="端口">
            <NInputNumber v-model:value="port" :min="1" :max="65535" placeholder="7890" style="width: 120px" />
          </NFormItem>
        </NSpace>

        <!-- 认证信息 -->
        <NSpace>
          <NFormItem label="用户名 (可选)">
            <NInput v-model:value="username" placeholder="用户名" style="width: 150px" />
          </NFormItem>
          <NFormItem label="密码 (可选)">
            <NInput v-model:value="password" type="password" placeholder="密码" style="width: 150px" />
          </NFormItem>
        </NSpace>

        <NSpace v-if="hasAuth" align="center">
          <NTag type="info" size="small">已配置认证信息</NTag>
        </NSpace>
      </template>

      <!-- 测试结果 -->
      <NSpace v-if="testResult" align="center">
        <span>测试结果:</span>
        <NTag :type="testResult.success ? 'success' : 'error'">
          {{ testResult.message }}
          <template v-if="testResult.latency"> ({{ testResult.latency }}ms)</template>
        </NTag>
      </NSpace>

      <!-- 操作按钮 -->
      <NSpace>
        <NButton type="primary" :loading="saving" @click="saveConfig">
          保存配置
        </NButton>
        <NButton :loading="testing" :disabled="!isConfigured" @click="testConnection">
          测试连接
        </NButton>
        <NButton v-if="isConfigured" @click="clearConfig">
          清除配置
        </NButton>
      </NSpace>
    </NSpace>
  </NCard>
</template>
