<script setup lang="ts">
import { computed, watch } from 'vue'
import { useRoute } from 'vue-router'
import { NConfigProvider, NMessageProvider, NDialogProvider, zhCN, dateZhCN } from 'naive-ui'
import { useTheme } from '@/composables/useTheme'
import { useWebSocket } from '@/composables/useWebSocket'
import { useAuthStore } from '@/stores/auth'
import AppLayout from '@/components/layout/AppLayout.vue'

const route = useRoute()
const { theme, themeOverrides } = useTheme()
const { connect, disconnect } = useWebSocket()
const authStore = useAuthStore()

// 登录页面不显示布局
const showLayout = computed(() => route.name !== 'login')

// 只为已认证会话建立连接，注销后立即断开。
watch(
  () => authStore.isAuthenticated,
  (authenticated) => {
    if (authenticated) {
      connect()
    } else {
      disconnect()
    }
  },
  { immediate: true },
)
</script>

<template>
  <NConfigProvider
    :theme="theme"
    :theme-overrides="themeOverrides"
    :locale="zhCN"
    :date-locale="dateZhCN"
  >
    <NDialogProvider>
      <NMessageProvider>
        <AppLayout v-if="showLayout" />
        <router-view v-else />
      </NMessageProvider>
    </NDialogProvider>
  </NConfigProvider>
</template>

<style>
html, body {
  margin: 0;
  padding: 0;
  background: var(--ios-bg-primary, #F2F2F7);
  transition: background-color 0.3s ease;
}

html.dark, body.dark {
  background: var(--ios-bg-primary, #000000);
}

#app {
  font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", "SF Pro Text", "Helvetica Neue", "PingFang SC", sans-serif;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
}

/* iOS 风格滚动条 */
::-webkit-scrollbar {
  width: 8px;
  height: 8px;
}

::-webkit-scrollbar-track {
  background: transparent;
}

::-webkit-scrollbar-thumb {
  background: rgba(0, 0, 0, 0.15);
  border-radius: 4px;
}

::-webkit-scrollbar-thumb:hover {
  background: rgba(0, 0, 0, 0.25);
}

.dark ::-webkit-scrollbar-thumb {
  background: rgba(255, 255, 255, 0.15);
}

.dark ::-webkit-scrollbar-thumb:hover {
  background: rgba(255, 255, 255, 0.25);
}

/* 全局过渡动画 - iOS 风格 */
* {
  transition: background-color 0.25s ease, border-color 0.25s ease, color 0.25s ease, box-shadow 0.25s ease;
}

/* 禁用某些元素的过渡 */
input, textarea, select, button {
  transition: none;
}
</style>
