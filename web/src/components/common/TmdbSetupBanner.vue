<script setup lang="ts">
import { ref, onMounted } from 'vue'
import {
  NAlert,
  NButton,
  NIcon,
} from 'naive-ui'
import {
  KeyOutline,
  SettingsOutline,
  CheckmarkCircleOutline,
} from '@vicons/ionicons5'
import { configApi } from '@/api/config'
import type { ApiTokenStatus } from '@/api/types'
import TmdbSetupWizard from '@/components/settings/TmdbSetupWizard.vue'

const props = defineProps<{
  // 是否紧凑模式（用于设置页面）
  compact?: boolean
}>()

const emit = defineEmits<{
  (e: 'configured'): void
}>()

// 状态
const loading = ref(true)
const tokenStatus = ref<ApiTokenStatus | null>(null)
const showWizard = ref(false)
const dismissed = ref(false)

// 是否需要显示配置提示
const shouldShow = () => {
  if (dismissed.value) return false
  if (!tokenStatus.value) return false
  // 未配置或配置无效时显示
  return !tokenStatus.value.is_configured || tokenStatus.value.is_valid === false
}

// 获取状态信息
const getStatusInfo = () => {
  if (!tokenStatus.value?.is_configured) {
    return {
      type: 'warning' as const,
      title: '尚未配置 TMDB API',
      desc: '需要配置 TMDB API Token 才能使用刮削功能',
    }
  }
  if (tokenStatus.value.is_valid === false) {
    return {
      type: 'error' as const,
      title: 'TMDB API Token 无效',
      desc: tokenStatus.value.error_message || '请检查或重新配置 Token',
    }
  }
  return {
    type: 'success' as const,
    title: 'TMDB API 已配置',
    desc: '可以正常使用刮削功能',
  }
}

// 加载状态
const loadStatus = async () => {
  loading.value = true
  try {
    tokenStatus.value = await configApi.getApiTokenStatus()
  } catch (error) {
    console.error('获取 TMDB 配置状态失败:', error)
  } finally {
    loading.value = false
  }
}

// 关闭提示（本次会话内）
const handleDismiss = () => {
  dismissed.value = true
}

// 打开配置向导
const openWizard = () => {
  showWizard.value = true
}

// 配置成功回调
const handleSuccess = () => {
  loadStatus()
  emit('configured')
}

onMounted(() => {
  loadStatus()
})

// 暴露方法供父组件调用
defineExpose({
  loadStatus,
  openWizard,
})
</script>

<template>
  <div v-if="!loading && shouldShow()" class="tmdb-banner" :class="{ compact }">
    <NAlert
      :type="getStatusInfo().type"
      :show-icon="true"
      closable
      @close="handleDismiss"
    >
      <template #icon>
        <NIcon :component="tokenStatus?.is_configured ? SettingsOutline : KeyOutline" />
      </template>

      <div class="banner-content">
        <div class="banner-text">
          <span class="banner-title">{{ getStatusInfo().title }}</span>
          <span class="banner-desc">{{ getStatusInfo().desc }}</span>
        </div>
        <NButton
          type="primary"
          size="small"
          @click="openWizard"
        >
          <template #icon>
            <NIcon :component="SettingsOutline" />
          </template>
          {{ tokenStatus?.is_configured ? '重新配置' : '立即配置' }}
        </NButton>
      </div>
    </NAlert>

    <!-- 配置向导 -->
    <TmdbSetupWizard
      v-model:show="showWizard"
      :initial-status="tokenStatus"
      @success="handleSuccess"
    />
  </div>

  <!-- 已配置成功的简洁提示（可选显示） -->
  <div
    v-else-if="!loading && tokenStatus?.is_configured && tokenStatus?.is_valid && compact"
    class="tmdb-configured"
  >
    <NIcon :component="CheckmarkCircleOutline" color="#34C759" :size="16" />
    <span>TMDB API 已配置</span>
  </div>
</template>

<style scoped>
.tmdb-banner {
  margin-bottom: 16px;
}

.tmdb-banner.compact {
  margin-bottom: 12px;
}

.banner-content {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
}

.banner-text {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.banner-title {
  font-size: 14px;
  font-weight: 600;
  color: inherit;
}

.banner-desc {
  font-size: 13px;
  opacity: 0.85;
}

/* 已配置状态 */
.tmdb-configured {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 12px;
  background: rgba(52, 199, 89, 0.1);
  border-radius: 8px;
  font-size: 13px;
  color: #34C759;
  font-weight: 500;
  margin-bottom: 12px;
}

/* 响应式 */
@media (max-width: 480px) {
  .banner-content {
    flex-direction: column;
    align-items: flex-start;
  }
}
</style>
