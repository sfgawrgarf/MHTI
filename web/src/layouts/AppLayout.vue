<script setup lang="ts">
/**
 * AppLayout — 应用外壳
 *
 * 布局范式：**居中单栏**。顶部条（品牌 / 主导航 / 工具）+ 内容区
 * （最大宽度 --shell-max-w 居中，超宽屏不再把内容拉成一条长横线）。
 * 移动端顶部条收起主导航，导航下沉到固定底部 TabBar。
 *
 * 与旧实现的区别：不再有侧边栏与移动端抽屉。一级导航只有 5~6 项，
 * 横向排布即可全覆盖，省下的 240px 让内容区获得更宽的版面；
 * 隐藏页（/security）通过用户菜单与命令面板进入，URL 保持不变。
 *
 * 本组件只做编排：导航与账户动作统一在这里落到副作用，子组件保持纯展示。
 */
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useDialog } from 'naive-ui'
import { useAuthStore } from '@/stores/auth'
import { useMobileLayout } from '@/shared/composables/useMobileLayout'
import { useTheme } from '@/shared/composables/useTheme'
import { AdminConfigDrawer } from '@/modules/auth'
import type { CommandAction } from './types'
import AppTopBar from './AppTopBar.vue'
import CommandPalette from './CommandPalette.vue'
import MobileTabBar from './MobileTabBar.vue'

const router = useRouter()
const dialog = useDialog()
const authStore = useAuthStore()
const { isMobile } = useMobileLayout()
const { isDark, toggleTheme } = useTheme()

const commandOpen = ref(false)
const accountDrawerOpen = ref(false)

/** 命令面板里的动作项（跳转项由面板自己按路由 meta 生成） */
const commandActions = computed<CommandAction[]>(() => [
  { key: 'account', label: '账户与安全', hint: '资料、密码、会话与日志', keywords: ['profile', '账户'] },
  { key: 'theme', label: isDark.value ? '切换到亮色模式' : '切换到暗色模式', keywords: ['theme', '主题'] },
  { key: 'logout', label: '退出登录', keywords: ['logout', '登出'] },
])

function runCommandAction(key: string) {
  switch (key) {
    case 'account':
      accountDrawerOpen.value = true
      break
    case 'theme':
      toggleTheme()
      break
    case 'logout':
      confirmLogout()
      break
    default:
      break
  }
}

/** 退出登录前二次确认（不可逆操作不做静默执行） */
function confirmLogout() {
  dialog.warning({
    title: '确认退出',
    content: '确定要退出登录吗？',
    positiveText: '确定',
    negativeText: '取消',
    onPositiveClick: async () => {
      await authStore.logout()
      router.push('/login')
    },
  })
}

// 移动端底部 TabBar 会占位，内容区需预留等高空间
const contentPadding = computed(() =>
  isMobile.value
    ? `var(--space-6) var(--shell-gutter) calc(var(--shell-tabbar-h) + var(--space-6) + env(safe-area-inset-bottom, 0px))`
    : `var(--space-8) var(--shell-gutter) var(--space-16)`,
)
</script>

<template>
  <div class="app-shell">
    <AppTopBar
      @open-command="commandOpen = true"
      @open-account="accountDrawerOpen = true"
      @logout="confirmLogout"
    />

    <main class="app-main" :style="{ padding: contentPadding }">
      <div class="app-content">
        <router-view v-slot="{ Component }">
          <Transition name="page-fade" mode="out-in">
            <component :is="Component" />
          </Transition>
        </router-view>
      </div>
    </main>

    <MobileTabBar v-if="isMobile" />

    <CommandPalette
      v-model:show="commandOpen"
      :actions="commandActions"
      @run="runCommandAction"
    />

    <AdminConfigDrawer v-model:show="accountDrawerOpen" />
  </div>
</template>

<style scoped>
.app-shell {
  display: flex;
  flex-direction: column;
  min-height: 100vh;
  background: var(--bg-page);
}

.app-main {
  flex: 1;
  min-width: 0;
}

/* 内容居中且限宽：宽屏下正文行宽可控，不再被拉伸到 1600px+ */
.app-content {
  max-width: var(--shell-max-w);
  margin: 0 auto;
}
</style>
