<script setup lang="ts">
/**
 * 登录 / 注册表单面板
 *
 * 与旧实现的差别：
 * - 字段改用**常驻 label**（原来只有 placeholder）。placeholder 在输入后即消失，
 *   用户无法回看当前字段是什么，出错时也不清楚改哪一项。
 * - 去掉 `round` 胶囊输入框。胶囊形态与全站其余输入框（8px 圆角）不一致，
 *   且旧代码还要用 `--n-border-radius: 12px !important` 去覆盖它，属于自相矛盾。
 */
import { reactive, ref, toRefs } from 'vue'
import { NButton, NForm, NFormItem, NIcon, NInput, NSelect } from 'naive-ui'
import { LockClosedOutline, PersonOutline } from '@vicons/ionicons5'
import type { FormInst, FormRules, SelectOption } from 'naive-ui'
import type { LoginFormData, RegisterFormData } from '@/modules/auth/hooks/useLogin'

const props = defineProps<{
  isRegisterMode: boolean
  loading: boolean
  loginForm: LoginFormData
  registerForm: RegisterFormData
  loginRules: FormRules
  registerRules: FormRules
  expireOptions: SelectOption[]
}>()

const { isRegisterMode, loading, loginRules, registerRules, expireOptions } = toRefs(props)
const loginForm = reactive(props.loginForm)
const registerForm = reactive(props.registerForm)

const emit = defineEmits<{
  login: []
  register: []
}>()

// 两个分支的 NForm 共用同一实例（同一时刻只渲染其一）
const formRef = ref<FormInst | null>(null)

defineExpose({
  validate: () => formRef.value?.validate(),
})
</script>

<template>
  <!-- 注册表单 -->
  <NForm
    v-if="isRegisterMode"
    ref="formRef"
    :model="registerForm"
    :rules="registerRules"
    label-placement="top"
    :show-require-mark="false"
    class="auth-form"
  >
    <NFormItem label="用户名" path="username">
      <NInput v-model:value="registerForm.username" :input-props="{ 'aria-label': '用户名' }" placeholder="用于登录的账号名" size="large">
        <template #prefix>
          <NIcon :component="PersonOutline" :size="16" class="input-icon" />
        </template>
      </NInput>
    </NFormItem>

    <NFormItem label="密码" path="password">
      <NInput
        v-model:value="registerForm.password"
        :input-props="{ 'aria-label': '密码' }"
        type="password"
        placeholder="至少 6 位"
        size="large"
        show-password-on="click"
      >
        <template #prefix>
          <NIcon :component="LockClosedOutline" :size="16" class="input-icon" />
        </template>
      </NInput>
    </NFormItem>

    <NFormItem label="确认密码" path="confirmPassword">
      <NInput
        v-model:value="registerForm.confirmPassword"
        :input-props="{ 'aria-label': '确认密码' }"
        type="password"
        placeholder="再次输入密码"
        size="large"
        show-password-on="click"
        @keyup.enter="emit('register')"
      >
        <template #prefix>
          <NIcon :component="LockClosedOutline" :size="16" class="input-icon" />
        </template>
      </NInput>
    </NFormItem>

    <NButton
      type="primary"
      size="large"
      block
      :loading="loading"
      class="submit"
      @click="emit('register')"
    >
      创建并开始
    </NButton>
  </NForm>

  <!-- 登录表单 -->
  <NForm
    v-else
    ref="formRef"
    :model="loginForm"
    :rules="loginRules"
    label-placement="top"
    :show-require-mark="false"
    class="auth-form"
  >
    <NFormItem label="用户名" path="username">
      <NInput v-model:value="loginForm.username" :input-props="{ 'aria-label': '用户名' }" placeholder="输入用户名" size="large">
        <template #prefix>
          <NIcon :component="PersonOutline" :size="16" class="input-icon" />
        </template>
      </NInput>
    </NFormItem>

    <NFormItem label="密码" path="password">
      <NInput
        v-model:value="loginForm.password"
        :input-props="{ 'aria-label': '密码' }"
        type="password"
        placeholder="输入密码"
        size="large"
        show-password-on="click"
        @keyup.enter="emit('login')"
      >
        <template #prefix>
          <NIcon :component="LockClosedOutline" :size="16" class="input-icon" />
        </template>
      </NInput>
    </NFormItem>

    <NFormItem label="保持登录" :show-feedback="false">
      <NSelect
        v-model:value="loginForm.expire_option"
        aria-label="保持登录时长"
        :options="expireOptions"
        size="large"
        placeholder="选择有效期"
      />
    </NFormItem>

    <NButton
      type="primary"
      size="large"
      block
      :loading="loading"
      class="submit"
      @click="emit('login')"
    >
      登录
    </NButton>
  </NForm>
</template>

<style scoped>
.auth-form :deep(.n-form-item) {
  margin-bottom: var(--space-4);
}

/* 让校验信息出现时也占据固定高度，避免提交瞬间表单整体跳动 */
.auth-form :deep(.n-form-item-feedback-wrapper) {
  min-height: 18px;
}

.auth-form :deep(.n-form-item-label) {
  font-size: var(--text-sm);
  color: var(--text-2);
}

.input-icon {
  color: var(--text-3);
}

.submit {
  margin-top: var(--space-2);
}
</style>
