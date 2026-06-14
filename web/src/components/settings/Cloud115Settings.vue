<script setup lang="ts">
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import {
  NCard,
  NSpace,
  NButton,
  NSelect,
  NTag,
  NModal,
  NSpin,
  NAlert,
  useMessage,
} from 'naive-ui'
import { configApi } from '@/api/config'
import type {
  Cloud115Status,
  Cloud115DeviceOption,
} from '@/api/types'

const message = useMessage()

// 配置状态
const loading = ref(false)
const starting = ref(false)
const loggingOut = ref(false)

// 状态数据
const status = ref<Cloud115Status | null>(null)
const devices = ref<Cloud115DeviceOption[]>([])
const selectedApp = ref('alipaymini')

// 扫码登录 Modal
const showQrModal = ref(false)
const qrDataUrl = ref('')
const qrUid = ref('')
const qrStatusText = ref('')
const qrStatusType = ref<'pending' | 'scanned' | 'success' | 'expired' | 'canceled' | 'unknown'>('pending')
let pollTimer: ReturnType<typeof setTimeout> | null = null

// 设备下拉选项（分组：标准 / 别名）
const deviceOptions = computed(() => {
  const standard = devices.value
    .filter((d) => d.group === 'standard')
    .map((d) => ({ label: d.label, value: d.value }))
  const alias = devices.value
    .filter((d) => d.group === 'alias')
    .map((d) => ({ label: d.label, value: d.value }))
  const groups: { type: 'group'; label: string; key: string; children: { label: string; value: string }[] }[] = []
  if (standard.length) groups.push({ type: 'group', label: '标准设备', key: 'standard', children: standard })
  if (alias.length) groups.push({ type: 'group', label: '别名', key: 'alias', children: alias })
  return groups
})

// 状态文案映射
const STATUS_TEXT: Record<string, string> = {
  pending: '等待扫码',
  scanned: '已扫码，请在手机上确认',
  success: '登录成功',
  expired: '二维码已过期',
  canceled: '已取消',
  unknown: '未知状态',
}

const STATUS_TAG_TYPE: Record<string, 'success' | 'warning' | 'error' | 'default' | 'info'> = {
  pending: 'default',
  scanned: 'info',
  success: 'success',
  expired: 'error',
  canceled: 'warning',
  unknown: 'default',
}

// 加载状态
const loadStatus = async () => {
  loading.value = true
  try {
    status.value = await configApi.get115Status()
    if (status.value.app) selectedApp.value = status.value.app
  } catch (error) {
    console.error('加载 115 状态失败:', error)
  } finally {
    loading.value = false
  }
}

// 加载设备列表
const loadDevices = async () => {
  try {
    devices.value = await configApi.get115Devices()
  } catch (error) {
    console.error('加载 115 设备列表失败:', error)
  }
}

// 生成二维码图片（data URL）
const generateQrImage = async (text: string): Promise<string> => {
  const QRCode = (await import('qrcode')).default
  return await QRCode.toDataURL(text, { width: 240, margin: 1 })
}

// 开始扫码登录
const startQrLogin = async () => {
  if (starting.value) return
  starting.value = true
  qrStatusText.value = '正在生成二维码...'
  qrStatusType.value = 'pending'
  qrDataUrl.value = ''
  try {
    const session = await configApi.start115QrLogin(selectedApp.value)
    qrUid.value = session.uid
    try {
      qrDataUrl.value = await generateQrImage(session.qrcode_url)
    } catch (e) {
      // qrcode 包加载失败时降级：直接展示 URL 文本
      console.warn('二维码渲染失败，降级为文本:', e)
      qrDataUrl.value = ''
    }
    qrStatusText.value = STATUS_TEXT['pending']
    showQrModal.value = true
    startPolling()
  } catch (error: any) {
    message.error(error?.response?.data?.detail || '生成二维码失败')
  } finally {
    starting.value = false
  }
}

// 轮询登录状态
const startPolling = () => {
  stopPolling()
  const poll = async () => {
    if (!qrUid.value || !showQrModal.value) return
    try {
      const result = await configApi.poll115QrLogin(qrUid.value, selectedApp.value)
      qrStatusType.value = (result.status as typeof qrStatusType.value) || 'unknown'
      qrStatusText.value = result.message || STATUS_TEXT[result.status] || STATUS_TEXT['unknown']

      if (result.is_logged_in || result.status === 'success') {
        message.success('115 网盘登录成功')
        stopPolling()
        showQrModal.value = false
        await loadStatus()
        return
      }
      if (result.status === 'expired' || result.status === 'canceled') {
        stopPolling()
        return
      }
      pollTimer = setTimeout(poll, 1500)
    } catch (error: any) {
      console.error('轮询登录状态失败:', error)
      // 出错后稍后重试，避免无限报错
      pollTimer = setTimeout(poll, 3000)
    }
  }
  pollTimer = setTimeout(poll, 1500)
}

const stopPolling = () => {
  if (pollTimer) {
    clearTimeout(pollTimer)
    pollTimer = null
  }
}

// 关闭二维码 Modal
const closeQrModal = () => {
  showQrModal.value = false
  stopPolling()
}

// 退出登录
const clearLogin = async () => {
  if (loggingOut.value) return
  loggingOut.value = true
  try {
    const result = await configApi.clear115Login()
    if (result.success) {
      message.success(result.message || '已退出登录')
    } else {
      message.warning(result.message || '退出失败')
    }
    await loadStatus()
  } catch (error: any) {
    message.error(error?.response?.data?.detail || '退出登录失败')
  } finally {
    loggingOut.value = false
  }
}

// 登录设备显示名
const currentDeviceLabel = computed(() => {
  if (!status.value) return ''
  const match = devices.value.find((d) => d.value === status.value!.app)
  return match?.label || status.value.app
})

onMounted(() => {
  loadStatus()
  loadDevices()
})

onBeforeUnmount(() => {
  stopPolling()
})
</script>

<template>
  <NCard title="115 网盘" size="small">
    <NSpace vertical size="large">
      <!-- 登录状态 -->
      <NSpace align="center">
        <span>登录状态:</span>
        <NTag v-if="status?.is_logged_in" type="success">已登录</NTag>
        <NTag v-else type="default">未登录</NTag>
        <template v-if="status?.is_logged_in">
          <span style="color: #999">设备: {{ currentDeviceLabel }}</span>
        </template>
      </NSpace>

      <template v-if="!status?.is_logged_in">
        <NAlert type="info" :show-icon="false">
          选择登录设备后点击"扫码登录"，使用 115 手机 App 扫描二维码完成授权。
        </NAlert>

        <!-- 设备选择 -->
        <div>
          <div style="margin-bottom: 8px; color: #999">登录设备</div>
          <NSelect
            v-model:value="selectedApp"
            :options="deviceOptions"
            :loading="loading"
            style="max-width: 360px"
            placeholder="选择登录设备"
          />
        </div>

        <NButton type="primary" :loading="starting" @click="startQrLogin">
          扫码登录
        </NButton>
      </template>

      <template v-else>
        <NButton :loading="loggingOut" @click="clearLogin">
          退出登录
        </NButton>
      </template>
    </NSpace>

    <!-- 二维码登录 Modal -->
    <NModal
      v-model:show="showQrModal"
      preset="card"
      title="115 网盘扫码登录"
      style="width: 360px"
      :mask-closable="false"
      @close="closeQrModal"
    >
      <NSpace vertical align="center" size="large">
        <NAlert :type="STATUS_TAG_TYPE[qrStatusType]" :show-icon="false" style="width: 100%">
          {{ qrStatusText }}
        </NAlert>

        <NSpin v-if="starting && !qrDataUrl" size="large" />

        <img
          v-else-if="qrDataUrl"
          :src="qrDataUrl"
          alt="115 登录二维码"
          style="width: 240px; height: 240px"
        />

        <NAlert
          v-if="qrStatusType === 'expired' || qrStatusType === 'canceled'"
          type="warning"
          :show-icon="false"
        >
          请重新生成二维码登录
        </NAlert>

        <NButton @click="closeQrModal">关闭</NButton>
      </NSpace>
    </NModal>
  </NCard>
</template>
