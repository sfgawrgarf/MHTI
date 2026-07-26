<template>
  <div class="login-page" :class="{ 'login-page--dark': isDark }">
    <!-- 主题切换 - 右下角 -->
    <div class="theme-switch">
      <NButton quaternary circle @click="toggleTheme">
        <template #icon>
          <NIcon :component="isDark ? SunnyOutline : MoonOutline" />
        </template>
      </NButton>
    </div>

    <!-- 登录卡片 -->
    <div class="login-card">
      <div v-if="checking" class="login-card__loading">
        <NSpin size="large" />
      </div>

      <!-- 注册表单 -->
      <template v-else-if="isRegisterMode">
        <div class="login-card__header">
          <h1 class="login-card__title">创建账号</h1>
          <p class="login-card__subtitle">首次使用，请设置管理员</p>
        </div>

        <NForm
          ref="formRef"
          :model="registerForm"
          :rules="registerRules"
          class="login-form"
        >
          <NFormItem path="username" :show-label="false">
            <NInput
              v-model:value="registerForm.username"
              placeholder="用户名"
              size="large"
              round
            >
              <template #prefix>
                <NIcon :component="PersonOutline" class="input-icon" />
              </template>
            </NInput>
          </NFormItem>

          <NFormItem path="password" :show-label="false">
            <NInput
              v-model:value="registerForm.password"
              type="password"
              placeholder="密码"
              size="large"
              round
              show-password-on="click"
            >
              <template #prefix>
                <NIcon :component="LockClosedOutline" class="input-icon" />
              </template>
            </NInput>
          </NFormItem>

          <NFormItem path="confirmPassword" :show-label="false">
            <NInput
              v-model:value="registerForm.confirmPassword"
              type="password"
              placeholder="确认密码"
              size="large"
              round
              show-password-on="click"
              @keyup.enter="handleRegister"
            >
              <template #prefix>
                <NIcon :component="LockClosedOutline" class="input-icon" />
              </template>
            </NInput>
          </NFormItem>

          <NButton
            type="primary"
            size="large"
            block
            round
            :loading="loading"
            class="submit-btn"
            @click="handleRegister"
          >
            创建并开始
          </NButton>
        </NForm>
      </template>

      <!-- 登录表单 -->
      <template v-else>
        <div class="login-card__header">
          <h1 class="login-card__title">欢迎回来</h1>
          <p class="login-card__subtitle">登录以继续</p>
        </div>

        <NForm
          ref="formRef"
          :model="loginForm"
          :rules="loginRules"
          class="login-form"
        >
          <NFormItem path="username" :show-label="false">
            <NInput
              v-model:value="loginForm.username"
              placeholder="用户名"
              size="large"
              round
            >
              <template #prefix>
                <NIcon :component="PersonOutline" class="input-icon" />
              </template>
            </NInput>
          </NFormItem>

          <NFormItem path="password" :show-label="false">
            <NInput
              v-model:value="loginForm.password"
              type="password"
              placeholder="密码"
              size="large"
              round
              show-password-on="click"
              @keyup.enter="handleLogin"
            >
              <template #prefix>
                <NIcon :component="LockClosedOutline" class="input-icon" />
              </template>
            </NInput>
          </NFormItem>

          <NFormItem :show-label="false">
            <NSelect
              v-model:value="loginForm.expire_option"
              :options="expireOptions"
              size="large"
              placeholder="登录有效期"
              class="expire-select"
            />
          </NFormItem>

          <NButton
            type="primary"
            size="large"
            block
            round
            :loading="loading"
            class="submit-btn"
            @click="handleLogin"
          >
            登录
          </NButton>
        </NForm>
      </template>
    </div>
  </div>
</template>

<script setup lang="ts">
import {
  NForm,
  NFormItem,
  NInput,
  NButton,
  NSelect,
  NIcon,
  NSpin,
} from 'naive-ui'
import {
  PersonOutline,
  LockClosedOutline,
  SunnyOutline,
  MoonOutline,
} from '@vicons/ionicons5'
import { useLogin } from '@/composables/useLogin'
import { useTheme } from '@/composables/useTheme'
import { expireOptions as expireOptionsData } from '@/api/auth'

const {
  loginForm,
  registerForm,
  loginRules,
  registerRules,
  loading,
  checking,
  isRegisterMode,
  handleLogin,
  handleRegister,
} = useLogin()
const { isDark, toggleTheme } = useTheme()

const expireOptions = expireOptionsData.map((opt) => ({
  label: opt.label,
  value: opt.value,
}))
</script>

<style scoped>
.login-page {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
  background: linear-gradient(135deg, #f5f7fa 0%, #e4e8ec 100%);
  position: relative;
}

.login-page--dark {
  background: linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%);
}

/* 主题切换按钮 */
.theme-switch {
  position: fixed;
  bottom: 24px;
  right: 24px;
  z-index: 100;
}

/* 登录卡片 */
.login-card {
  width: 100%;
  max-width: 400px;
  padding: 48px 40px;
  border-radius: 24px;
  background: #ffffff;
  box-shadow:
    0 25px 50px -12px rgba(0, 0, 0, 0.15),
    0 0 0 1px rgba(0, 0, 0, 0.02);
  animation: cardSlideUp 0.5s ease-out;
}

.login-page--dark .login-card {
  background: var(--ios-bg-secondary);
  box-shadow:
    0 25px 50px -12px rgba(0, 0, 0, 0.5),
    0 0 0 1px rgba(255, 255, 255, 0.05);
}

@keyframes cardSlideUp {
  from {
    opacity: 0;
    transform: translateY(30px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

.login-card__loading {
  display: flex;
  justify-content: center;
  padding: 60px 0;
}

.login-card__header {
  text-align: center;
  margin-bottom: 32px;
}

.login-card__title {
  margin: 0 0 8px;
  font-size: 28px;
  font-weight: 700;
  color: #1e293b;
  letter-spacing: -0.5px;
}

.login-page--dark .login-card__title {
  color: #f1f5f9;
}

.login-card__subtitle {
  margin: 0;
  font-size: 15px;
  color: #64748b;
}

.login-page--dark .login-card__subtitle {
  color: #94a3b8;
}

/* 表单样式 */
.login-form {
  display: flex;
  flex-direction: column;
}

.login-form :deep(.n-form-item) {
  margin-bottom: 16px;
}

.login-form :deep(.n-form-item-feedback-wrapper) {
  min-height: 20px;
}

/* 输入框样式 */
.login-form :deep(.n-input) {
  --n-height: 48px !important;
  --n-font-size: 15px !important;
}

.login-form :deep(.n-input--round) {
  --n-border-radius: 12px !important;
}

.input-icon {
  color: #94a3b8;
  font-size: 18px;
}

/* 选择器样式 */
.expire-select :deep(.n-base-selection) {
  --n-height: 48px !important;
  --n-font-size: 15px !important;
  --n-border-radius: 12px !important;
}

/* 提交按钮 */
.submit-btn {
  --n-height: 48px !important;
  --n-font-size: 16px !important;
  --n-font-weight: 600 !important;
  margin-top: 8px;
}

.submit-btn:deep(.n-button--round) {
  --n-border-radius: 12px !important;
}

/* 响应式 */
@media (max-width: 480px) {
  .login-card {
    padding: 36px 28px;
    border-radius: 20px;
  }

  .login-card__title {
    font-size: 24px;
  }
}
</style>
