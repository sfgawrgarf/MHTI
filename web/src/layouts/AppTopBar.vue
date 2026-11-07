<script setup lang="ts">
/**
 * AppTopBar — 应用顶部条（Shell 第一层）
 *
 * 三段式：品牌区 / 主导航区 / 工具区。主导航项由路由 meta 派生
 * （唯一真源见 router/shell.ts），本组件不做任何硬编码菜单。
 *
 * 移动端隐藏主导航，改由 AppLayout 渲染底部 TabBar 承担导航；
 * 顶部条只保留品牌、搜索与用户入口。
 */
import { computed } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import { NIcon, NTooltip } from 'naive-ui'
import { MoonOutline, SearchOutline, SunnyOutline } from '@vicons/ionicons5'
import { buildMenuEntries, isNavActive } from '@/router/menu'
import { useMobileLayout } from '@/shared/composables/useMobileLayout'
import { useTheme } from '@/shared/composables/useTheme'
import AppUserMenu from './AppUserMenu.vue'

const emit = defineEmits<{
  /** 请求打开命令面板 */
  openCommand: []
  /** 请求打开账户配置抽屉 */
  openAccount: []
  logout: []
}>()

const route = useRoute()
const { isMobile } = useMobileLayout()
const { isDark, toggleTheme } = useTheme()

const navItems = computed(() => buildMenuEntries())

/** 主题按钮的无障碍与提示文案（同一处推导，避免两处不一致） */
const themeLabel = computed(() => (isDark.value ? '切换到亮色模式' : '切换到暗色模式'))
</script>

<template>
  <header class="topbar">
    <div class="topbar-inner">
      <!-- 品牌区 -->
      <RouterLink to="/" class="brand" aria-label="MHTI 主界面">
        <span class="brand-mark" aria-hidden="true">
          <svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
            <path
              d="M5 12.5L10 17.5L19 7"
              stroke="currentColor"
              stroke-width="2.4"
              stroke-linecap="round"
              stroke-linejoin="round"
            />
          </svg>
        </span>
        <span class="brand-name">MHTI</span>
      </RouterLink>

      <!-- 主导航区（桌面/平板） -->
      <nav v-if="!isMobile" class="topnav" aria-label="主导航">
        <RouterLink
          v-for="item in navItems"
          :key="item.path"
          :to="item.path"
          class="topnav-item"
          :class="{ 'is-active': isNavActive(item.path, route.path) }"
          :aria-current="isNavActive(item.path, route.path) ? 'page' : undefined"
        >
          {{ item.title }}
        </RouterLink>
      </nav>

      <!-- 工具区 -->
      <div class="topbar-tools">
        <button
          type="button"
          class="cmd-trigger"
          :aria-label="'打开搜索与跳转（快捷键 Ctrl 或 Command 加 K）'"
          @click="emit('openCommand')"
        >
          <NIcon :component="SearchOutline" :size="16" />
          <span v-if="!isMobile" class="cmd-trigger-text">搜索或跳转</span>
          <kbd v-if="!isMobile" class="cmd-trigger-kbd">⌘K</kbd>
        </button>

        <NTooltip trigger="hover">
          <template #trigger>
            <button
              type="button"
              class="icon-btn"
              :aria-label="themeLabel"
              @click="toggleTheme"
            >
              <NIcon :component="isDark ? SunnyOutline : MoonOutline" :size="18" />
            </button>
          </template>
          {{ themeLabel }}
        </NTooltip>

        <span class="tool-divider" aria-hidden="true" />

        <AppUserMenu @open-account="emit('openAccount')" @logout="emit('logout')" />
      </div>
    </div>
  </header>
</template>

<style scoped>
.topbar {
  position: sticky;
  top: 0;
  z-index: var(--z-sticky);
  background: var(--bg-surface);
  border-bottom: 1px solid var(--border-1);
  /* 移动端刘海屏安全区 */
  padding-top: env(safe-area-inset-top, 0);
}

.topbar-inner {
  display: flex;
  align-items: center;
  gap: var(--space-6);
  height: var(--shell-header-h);
  max-width: var(--shell-max-w);
  margin: 0 auto;
  padding: 0 var(--shell-gutter);
}

/* -------------------- 品牌 -------------------- */
.brand {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-shrink: 0;
  color: var(--text-1);
}

.brand:hover {
  color: var(--text-1);
}

.brand-mark {
  display: grid;
  place-items: center;
  width: 26px;
  height: 26px;
  border-radius: var(--radius-sm);
  background: var(--brand-500);
  color: var(--text-on-brand);
}

.brand-mark svg {
  width: 16px;
  height: 16px;
}

.brand-name {
  font-size: var(--text-md);
  font-weight: var(--weight-semibold);
  letter-spacing: 0.02em;
}

/* -------------------- 主导航 -------------------- */
.topnav {
  display: flex;
  align-items: center;
  gap: var(--space-1);
  flex: 1;
  min-width: 0;
  height: 100%;
  overflow-x: auto;
  scrollbar-width: none;
}

.topnav::-webkit-scrollbar {
  display: none;
}

.topnav-item {
  position: relative;
  display: flex;
  align-items: center;
  height: 100%;
  padding: 0 var(--space-3);
  font-size: var(--text-base);
  font-weight: var(--weight-medium);
  color: var(--text-2);
  white-space: nowrap;
  transition: color var(--duration-fast) var(--ease-in-out);
}

.topnav-item:hover {
  color: var(--text-1);
}

/* 激活态用底部 2px 指示条：比整块高亮更轻，不挤压 56px 顶栏的呼吸感 */
.topnav-item.is-active {
  color: var(--text-1);
  font-weight: var(--weight-semibold);
}

.topnav-item.is-active::after {
  content: '';
  position: absolute;
  left: var(--space-3);
  right: var(--space-3);
  bottom: -1px;
  height: 2px;
  background: var(--brand-500);
  border-radius: var(--radius-full) var(--radius-full) 0 0;
}

/* -------------------- 工具区 -------------------- */
.topbar-tools {
  display: flex;
  align-items: center;
  gap: var(--space-1);
  flex-shrink: 0;
  margin-left: auto;
}

.cmd-trigger {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  height: 30px;
  padding: 0 var(--space-2) 0 var(--space-3);
  border: 1px solid var(--border-1);
  border-radius: var(--radius-md);
  background: var(--bg-subtle);
  color: var(--text-2);
  font-family: inherit;
  font-size: var(--text-sm);
  cursor: pointer;
  transition:
    border-color var(--duration-fast) var(--ease-in-out),
    color var(--duration-fast) var(--ease-in-out),
    background-color var(--duration-fast) var(--ease-in-out);
}

.cmd-trigger:hover {
  border-color: var(--border-2);
  color: var(--text-1);
  background: var(--bg-surface);
}

.cmd-trigger-text {
  min-width: 84px;
  text-align: left;
}

.cmd-trigger-kbd {
  padding: 1px 5px;
  border: 1px solid var(--border-1);
  border-radius: var(--radius-xs);
  background: var(--bg-surface);
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--text-3);
}

.icon-btn {
  display: grid;
  place-items: center;
  width: 30px;
  height: 30px;
  border: none;
  border-radius: var(--radius-md);
  background: transparent;
  color: var(--text-2);
  cursor: pointer;
  transition:
    background-color var(--duration-fast) var(--ease-in-out),
    color var(--duration-fast) var(--ease-in-out);
}

.icon-btn:hover {
  background: var(--bg-hover);
  color: var(--text-1);
}

.tool-divider {
  width: 1px;
  height: 18px;
  margin: 0 var(--space-1);
  background: var(--border-1);
}

/* -------------------- 移动端 -------------------- */
@media (max-width: 767px) {
  .topbar-inner {
    gap: var(--space-3);
  }

  .brand-name {
    display: none;
  }
}
</style>
