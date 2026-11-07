<script setup lang="ts">
/**
 * 115 网盘扫码登录弹窗（settings 域私有）
 *
 * 从 Cloud115Settings 拆出：扫码是独立流程（生成二维码 → 长轮询 → 成功/过期），
 * 与「账号身份 / 会员 / 容量」的展示无关，混在一个文件里两边都改不动。
 *
 * 二维码图片在前端生成（qrcode 包按需加载），margin 必须留足 4 个模块（QR 规范
 * 的静默区）：暗色主题下白边不足时扫码器容易二值化失败报「二维码无效」；
 * 白边烘进 PNG 后也与主题无关。
 */
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { NAlert, NButton, NModal, NSpin } from 'naive-ui'
import { configApi } from '@/shared/api/config'
import type { Cloud115QrSession } from '@/shared/types/common'

const props = defineProps<{
  /** 非 null 即展示弹窗；由父组件调接口拿到 */
  session: Cloud115QrSession | null
  /** 父组件正在重新生成二维码 */
  restarting?: boolean
}>()

const emit = defineEmits<{
  'update:session': [session: Cloud115QrSession | null]
  /** 扫码确认成功（父组件据此刷新登录状态与账号信息） */
  success: []
  /** 用户要求换一张二维码（约 5 分钟失效，旧码再扫只会得到「无效」） */
  restart: []
}>()

const qrDataUrl = ref('')
const qrStatusText = ref('')
const qrStatusType = ref<'pending' | 'scanned' | 'success' | 'expired' | 'canceled' | 'unknown'>(
  'pending',
)
let pollTimer: ReturnType<typeof setTimeout> | null = null
let qrGeneration = 0
let disposed = false

const visible = computed({
  get: () => props.session !== null,
  set: (value: boolean) => {
    if (!value) emit('update:session', null)
  },
})

const STATUS_TEXT = {
  pending: '等待扫码',
  scanned: '已扫码，请在手机上确认',
  success: '登录成功',
  expired: '二维码已过期',
  canceled: '已取消',
  unknown: '未知状态',
}

const STATUS_ALERT_TYPE: Record<string, 'success' | 'warning' | 'error' | 'default' | 'info'> = {
  pending: 'default',
  scanned: 'info',
  success: 'success',
  expired: 'error',
  canceled: 'warning',
  unknown: 'default',
}

const generateQrImage = async (text: string): Promise<string> => {
  const QRCode = (await import('qrcode')).default
  return await QRCode.toDataURL(text, { width: 240, margin: 4 })
}

const stopPolling = () => {
  if (pollTimer) {
    clearTimeout(pollTimer)
    pollTimer = null
  }
}

const startPolling = (session: Cloud115QrSession) => {
  stopPolling()
  const generation = qrGeneration
  const current = () => !disposed && generation === qrGeneration && props.session?.uid === session.uid
  // 连续失败才提示：115 的状态接口是长轮询（一次挂约 30s），偶发一次失败不必打扰用户
  let failCount = 0
  const poll = async () => {
    if (!current()) return
    try {
      const result = await configApi.poll115QrLogin(session.uid, session.app)
      if (!current()) return
      failCount = 0
      qrStatusType.value = (result.status as typeof qrStatusType.value) || 'unknown'
      qrStatusText.value =
        result.message ||
        (STATUS_TEXT as Record<string, string>)[result.status] ||
        STATUS_TEXT.unknown

      if (result.is_logged_in || result.status === 'success') {
        stopPolling()
        emit('update:session', null)
        emit('success')
        return
      }
      if (result.status === 'expired' || result.status === 'canceled') {
        stopPolling()
        return
      }
      pollTimer = setTimeout(poll, 1500)
    } catch (error) {
      if (!current()) return
      console.error('轮询登录状态失败:', error)
      // 轮询出错后稍后重试，避免无限报错；连续两次失败说明状态确实取不到，
      // 要让用户看得见，不能一直停在「等待扫码」上
      failCount += 1
      if (failCount >= 2) {
        qrStatusType.value = 'unknown'
        qrStatusText.value = '登录状态查询失败，正在重试…'
      }
      pollTimer = setTimeout(poll, 3000)
    }
  }
  pollTimer = setTimeout(poll, 1500)
}

/** 换二维码时重置状态；同一 uid 不重复轮询 */
watch(
  () => props.session,
  async (session) => {
    const generation = ++qrGeneration
    stopPolling()
    qrDataUrl.value = ''
    if (!session) return
    qrStatusType.value = 'pending'
    qrStatusText.value = '正在生成二维码…'
    try {
      const image = await generateQrImage(session.qrcode_url)
      if (disposed || generation !== qrGeneration) return
      qrDataUrl.value = image
    } catch (error) {
      if (disposed || generation !== qrGeneration) return
      // qrcode 包加载失败时降级：不展示图片，状态行仍在轮询
      console.warn('二维码渲染失败，降级为文本:', error)
      qrDataUrl.value = ''
    }
    qrStatusText.value = STATUS_TEXT.pending
    startPolling(session)
  },
  { immediate: true },
)

onBeforeUnmount(() => { disposed = true; qrGeneration++; stopPolling() })
</script>

<template>
  <NModal
    v-model:show="visible"
    preset="card"
    title="115 网盘扫码登录"
    class="qr-modal"
    :mask-closable="false"
  >
    <div class="qr-body">
      <NAlert :type="STATUS_ALERT_TYPE[qrStatusType]" :show-icon="false">
        {{ qrStatusText }}
      </NAlert>

      <NSpin v-if="!qrDataUrl && qrStatusType === 'pending'" size="large" />

      <img v-else-if="qrDataUrl" :src="qrDataUrl" alt="115 登录二维码" class="qr-image" />

      <NAlert
        v-if="qrStatusType === 'expired' || qrStatusType === 'canceled'"
        type="warning"
        :show-icon="false"
        title="请重新生成二维码登录"
      />

      <!-- 二维码约 5 分钟失效，用户可能想主动换一张（拿旧码去扫只会得到「无效」），
           因此按钮常驻而不是只在过期态出现 -->
      <div class="qr-actions">
        <NButton :loading="restarting" @click="emit('restart')">重新生成二维码</NButton>
        <NButton @click="visible = false">关闭</NButton>
      </div>
    </div>
  </NModal>
</template>

<style scoped>
.qr-modal {
  width: 360px;
}

.qr-body {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-4);
}

.qr-image {
  width: 240px;
  height: 240px;
}

.qr-actions {
  display: flex;
  gap: var(--space-2);
}
</style>
