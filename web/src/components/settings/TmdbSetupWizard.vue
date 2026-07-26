<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import {
  NModal,
  NCard,
  NSteps,
  NStep,
  NButton,
  NSpace,
  NIcon,
  NInput,
  NAlert,
  NSpin,
  NResult,
  useMessage,
} from 'naive-ui'
import {
  CloseOutline,
  KeyOutline,
  OpenOutline,
  CheckmarkCircleOutline,
  ArrowForwardOutline,
  ArrowBackOutline,
  ShieldCheckmarkOutline,
  AlertCircleOutline,
} from '@vicons/ionicons5'
import { configApi } from '@/api/config'
import type { ApiTokenStatus } from '@/api/types'

const props = defineProps<{
  show: boolean
  // 初始状态，用于跳过已完成的步骤
  initialStatus?: ApiTokenStatus | null
}>()

const emit = defineEmits<{
  (e: 'update:show', value: boolean): void
  (e: 'success'): void
}>()

const message = useMessage()

// 步骤状态
const currentStep = ref(1)
const tokenInput = ref('')
const verifying = ref(false)
const verifyResult = ref<{ success: boolean; message: string } | null>(null)

// 步骤配置
const steps = [
  { title: '获取 Token', icon: KeyOutline },
  { title: '验证配置', icon: ShieldCheckmarkOutline },
  { title: '完成设置', icon: CheckmarkCircleOutline },
]

// TMDB 注册和 API 页面链接
const TMDB_SIGNUP_URL = 'https://www.themoviedb.org/signup'
const TMDB_API_URL = 'https://www.themoviedb.org/settings/api'

// 是否可以继续下一步
const canProceed = computed(() => {
  if (currentStep.value === 1) {
    return true // 步骤1总是可以继续
  }
  if (currentStep.value === 2) {
    return tokenInput.value.trim().length > 0
  }
  return true
})

// 重置状态
const resetState = () => {
  currentStep.value = 1
  tokenInput.value = ''
  verifyResult.value = null
  verifying.value = false
}

// 关闭弹窗
const handleClose = () => {
  emit('update:show', false)
  // 延迟重置，避免动画问题
  setTimeout(resetState, 300)
}

// 上一步
const prevStep = () => {
  if (currentStep.value > 1) {
    currentStep.value--
    verifyResult.value = null
  }
}

// 下一步
const nextStep = async () => {
  if (currentStep.value === 2) {
    // 验证 Token
    await verifyToken()
  } else if (currentStep.value < 3) {
    currentStep.value++
  }
}

// 验证 Token
const verifyToken = async () => {
  if (!tokenInput.value.trim()) {
    message.warning('请输入 API Token')
    return
  }

  verifying.value = true
  verifyResult.value = null

  try {
    const response = await configApi.saveApiToken(tokenInput.value.trim())
    if (response.success && response.status?.is_valid) {
      verifyResult.value = { success: true, message: 'Token 验证成功！' }
      // 自动进入下一步
      setTimeout(() => {
        currentStep.value = 3
      }, 800)
    } else {
      verifyResult.value = {
        success: false,
        message: response.message || 'Token 验证失败，请检查是否正确',
      }
    }
  } catch (error) {
    verifyResult.value = {
      success: false,
      message: '验证过程中出错，请稍后重试',
    }
    console.error(error)
  } finally {
    verifying.value = false
  }
}

// 打开外部链接
const openExternal = (url: string) => {
  window.open(url, '_blank', 'noopener,noreferrer')
}

// 完成设置
const handleComplete = () => {
  emit('success')
  handleClose()
}

// 监听显示状态
watch(() => props.show, (show) => {
  if (show) {
    // 如果已配置，直接跳到验证步骤
    if (props.initialStatus?.is_configured && props.initialStatus?.is_valid) {
      currentStep.value = 3
    } else {
      resetState()
    }
  }
})
</script>

<template>
  <NModal
    :show="show"
    :mask-closable="false"
    transform-origin="center"
    @update:show="emit('update:show', $event)"
  >
    <NCard class="wizard-modal" :bordered="false">
      <!-- 头部 -->
      <template #header>
        <div class="wizard-header">
          <span class="wizard-title">配置 TMDB API</span>
          <NButton quaternary circle size="small" @click="handleClose">
            <template #icon>
              <NIcon :component="CloseOutline" />
            </template>
          </NButton>
        </div>
      </template>

      <!-- 步骤指示器 -->
      <div class="wizard-steps">
        <NSteps :current="currentStep" size="small">
          <NStep
            v-for="(step, index) in steps"
            :key="index"
            :title="step.title"
          />
        </NSteps>
      </div>

      <!-- 步骤内容 -->
      <div class="wizard-content">
        <!-- 步骤 1: 获取 Token -->
        <div v-if="currentStep === 1" class="step-content">
          <div class="intro-section">
            <div class="intro-icon">
              <NIcon :component="KeyOutline" :size="48" color="#007AFF" />
            </div>
            <h3 class="intro-title">获取 TMDB API Token</h3>
            <p class="intro-desc">
              MHTI 使用 TMDB (The Movie Database) 获取剧集信息。<br>
              您需要一个免费的 API Token 才能开始使用。
            </p>
          </div>

          <div class="guide-steps">
            <div class="guide-step">
              <span class="step-number">1</span>
              <div class="step-content-inner">
                <span class="step-title">注册 TMDB 账号</span>
                <span class="step-desc">如果您还没有账号，请先注册</span>
                <NButton
                  type="primary"
                  size="small"
                  @click="openExternal(TMDB_SIGNUP_URL)"
                >
                  <template #icon>
                    <NIcon :component="OpenOutline" />
                  </template>
                  前往注册
                </NButton>
              </div>
            </div>

            <div class="guide-step">
              <span class="step-number">2</span>
              <div class="step-content-inner">
                <span class="step-title">获取 API Token</span>
                <span class="step-desc">登录后进入 API 设置页面，复制 "API Read Access Token"</span>
                <NButton
                  type="primary"
                  size="small"
                  @click="openExternal(TMDB_API_URL)"
                >
                  <template #icon>
                    <NIcon :component="OpenOutline" />
                  </template>
                  打开 API 设置
                </NButton>
              </div>
            </div>

            <div class="guide-step">
              <span class="step-number">3</span>
              <div class="step-content-inner">
                <span class="step-title">复制长字符串 Token</span>
                <span class="step-desc">
                  找到 "API Read Access Token" 部分，复制以 "eyJ" 开头的长字符串
                </span>
              </div>
            </div>
          </div>

          <NAlert type="info" class="tip-alert">
            <template #icon>
              <NIcon :component="AlertCircleOutline" />
            </template>
            <strong>提示：</strong>请复制 "API Read Access Token"，而不是 "API Key"。<br>
            Token 通常以 "eyJ" 开头，是一个很长的字符串。
          </NAlert>
        </div>

        <!-- 步骤 2: 验证配置 -->
        <div v-else-if="currentStep === 2" class="step-content">
          <div class="intro-section compact">
            <div class="intro-icon small">
              <NIcon :component="ShieldCheckmarkOutline" :size="36" color="#34C759" />
            </div>
            <h3 class="intro-title">粘贴并验证 Token</h3>
            <p class="intro-desc">
              请将复制的 API Read Access Token 粘贴到下方输入框
            </p>
          </div>

          <div class="token-input-section">
            <NInput
              v-model:value="tokenInput"
              type="textarea"
              placeholder="粘贴 TMDB API Read Access Token (以 eyJ 开头)..."
              :rows="4"
              :disabled="verifying"
              class="token-input"
            />

            <!-- 验证状态 -->
            <div v-if="verifying" class="verify-status verifying">
              <NSpin size="small" />
              <span>正在验证 Token...</span>
            </div>

            <NAlert
              v-else-if="verifyResult"
              :type="verifyResult.success ? 'success' : 'error'"
              class="verify-result"
            >
              <template #icon>
                <NIcon :component="verifyResult.success ? CheckmarkCircleOutline : AlertCircleOutline" />
              </template>
              {{ verifyResult.message }}
            </NAlert>
          </div>

          <NAlert type="warning" class="tip-alert">
            <strong>注意：</strong>Token 将被安全加密存储在本地，不会上传到任何第三方服务。
          </NAlert>
        </div>

        <!-- 步骤 3: 完成设置 -->
        <div v-else class="step-content">
          <NResult
            status="success"
            title="配置完成！"
            description="TMDB API 已成功配置，您现在可以开始使用刮削功能了"
          >
            <template #icon>
              <div class="success-icon">
                <NIcon :component="CheckmarkCircleOutline" :size="72" color="#34C759" />
              </div>
            </template>
            <template #footer>
              <NSpace justify="center">
                <NButton type="primary" size="large" @click="handleComplete">
                  开始使用
                </NButton>
              </NSpace>
            </template>
          </NResult>
        </div>
      </div>

      <!-- 底部操作 -->
      <template v-if="currentStep < 3" #footer>
        <div class="wizard-footer">
          <div class="footer-left">
            <span class="step-indicator">步骤 {{ currentStep }} / 3</span>
          </div>
          <NSpace>
            <NButton v-if="currentStep > 1" @click="prevStep">
              <template #icon>
                <NIcon :component="ArrowBackOutline" />
              </template>
              上一步
            </NButton>
            <NButton
              type="primary"
              :disabled="!canProceed"
              :loading="verifying"
              @click="nextStep"
            >
              {{ currentStep === 2 ? '验证并继续' : '下一步' }}
              <template #icon>
                <NIcon :component="ArrowForwardOutline" />
              </template>
            </NButton>
          </NSpace>
        </div>
      </template>
    </NCard>
  </NModal>
</template>

<style scoped>
.wizard-modal {
  width: 560px;
  max-width: 95vw;
  border-radius: 20px;
  background: var(--ios-glass-bg-thick);
  backdrop-filter: blur(20px);
  -webkit-backdrop-filter: blur(20px);
  box-shadow: 0 25px 80px rgba(0, 0, 0, 0.2);
}

.wizard-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.wizard-title {
  font-size: 20px;
  font-weight: 700;
  color: var(--ios-text-primary);
}

.wizard-steps {
  padding: 0 8px 20px;
  border-bottom: 1px solid var(--ios-separator);
  margin-bottom: 20px;
}

.wizard-content {
  min-height: 380px;
  padding: 0 8px;
}

.step-content {
  animation: fadeIn 0.3s ease;
}

@keyframes fadeIn {
  from { opacity: 0; transform: translateY(10px); }
  to { opacity: 1; transform: translateY(0); }
}

/* 介绍区域 */
.intro-section {
  text-align: center;
  margin-bottom: 28px;
}

.intro-section.compact {
  margin-bottom: 20px;
}

.intro-icon {
  width: 88px;
  height: 88px;
  border-radius: 22px;
  background: rgba(0, 122, 255, 0.1);
  display: flex;
  align-items: center;
  justify-content: center;
  margin: 0 auto 16px;
}

.intro-icon.small {
  width: 64px;
  height: 64px;
  border-radius: 16px;
  background: rgba(52, 199, 89, 0.1);
  margin-bottom: 12px;
}

.intro-title {
  font-size: 20px;
  font-weight: 700;
  color: var(--ios-text-primary);
  margin: 0 0 8px;
}

.intro-desc {
  font-size: 14px;
  color: var(--ios-text-secondary);
  line-height: 1.6;
  margin: 0;
}

/* 引导步骤 */
.guide-steps {
  display: flex;
  flex-direction: column;
  gap: 16px;
  margin-bottom: 20px;
}

.guide-step {
  display: flex;
  gap: 14px;
  padding: 16px;
  background: var(--ios-bg-secondary);
  border-radius: 14px;
  transition: transform 0.2s ease;
}

.guide-step:hover {
  transform: translateX(4px);
}

.step-number {
  width: 28px;
  height: 28px;
  border-radius: 50%;
  background: var(--ios-blue);
  color: white;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 14px;
  font-weight: 700;
  flex-shrink: 0;
}

.step-content-inner {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.step-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--ios-text-primary);
}

.step-desc {
  font-size: 13px;
  color: var(--ios-text-secondary);
  line-height: 1.5;
}

/* Token 输入区域 */
.token-input-section {
  margin-bottom: 20px;
}

.token-input {
  margin-bottom: 12px;
}

.token-input :deep(.n-input__textarea-el) {
  font-family: 'SF Mono', Monaco, monospace;
  font-size: 13px;
}

.verify-status {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 16px;
  border-radius: 10px;
  font-size: 14px;
}

.verify-status.verifying {
  background: rgba(0, 122, 255, 0.1);
  color: var(--ios-blue);
}

.verify-result {
  margin-top: 12px;
}

/* 提示框 */
.tip-alert {
  border-radius: 12px;
}

.tip-alert :deep(.n-alert__content) {
  font-size: 13px;
  line-height: 1.6;
}

/* 成功图标 */
.success-icon {
  animation: bounceIn 0.5s ease;
}

@keyframes bounceIn {
  0% { transform: scale(0.5); opacity: 0; }
  50% { transform: scale(1.1); }
  100% { transform: scale(1); opacity: 1; }
}

/* 底部操作 */
.wizard-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-top: 8px;
}

.footer-left {
  display: flex;
  align-items: center;
  gap: 12px;
}

.step-indicator {
  font-size: 13px;
  color: var(--ios-text-tertiary);
}

/* 按钮样式 */
.wizard-modal :deep(.n-button--primary-type) {
  background: var(--ios-blue);
  box-shadow: 0 4px 14px rgba(0, 122, 255, 0.35);
}

.wizard-modal :deep(.n-button--primary-type:hover:not(:disabled)) {
  box-shadow: 0 6px 20px rgba(0, 122, 255, 0.45);
}

.wizard-modal :deep(.n-result) {
  padding: 20px 0;
}

.wizard-modal :deep(.n-result__header) {
  padding: 0;
}

.wizard-modal :deep(.n-result__icon) {
  margin-bottom: 16px;
}
</style>
