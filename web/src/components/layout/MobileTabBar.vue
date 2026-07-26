<script setup lang="ts">
import { computed, type Component } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NIcon } from 'naive-ui'
import {
  HomeOutline,
  Home,
  SearchOutline,
  Search,
  ListOutline,
  List,
  FolderOutline,
  Folder,
  SettingsOutline,
  Settings,
} from '@vicons/ionicons5'

/**
 * MobileTabBar 组件
 * iOS 风格底部标签栏，仅在移动端显示
 */

export interface TabBarItem {
  key: string
  label: string
  icon: Component
  iconActive: Component
  badge?: number | string
}

const props = withDefaults(defineProps<{
  /** 是否显示徽章动画 */
  animateBadge?: boolean
}>(), {
  animateBadge: true,
})

const route = useRoute()
const router = useRouter()

// TabBar 配置项
const tabItems: TabBarItem[] = [
  {
    key: '/',
    label: '主页',
    icon: HomeOutline,
    iconActive: Home,
  },
  {
    key: '/scan',
    label: '任务',
    icon: SearchOutline,
    iconActive: Search,
  },
  {
    key: '/history',
    label: '记录',
    icon: ListOutline,
    iconActive: List,
  },
  {
    key: '/files',
    label: '文件',
    icon: FolderOutline,
    iconActive: Folder,
  },
  {
    key: '/settings',
    label: '设置',
    icon: SettingsOutline,
    iconActive: Settings,
  },
]

// 当前激活的 tab
const activeKey = computed(() => {
  // 匹配当前路由，支持子路由匹配
  const path = route.path
  const matched = tabItems.find(item => {
    if (item.key === '/') {
      return path === '/'
    }
    return path.startsWith(item.key)
  })
  return matched?.key || '/'
})

// 处理 tab 切换
function handleTabClick(key: string) {
  if (key === activeKey.value) return

  // 触发触觉反馈
  triggerHaptic()

  router.push(key)
}

// 触发触觉反馈
function triggerHaptic() {
  if ('vibrate' in navigator) {
    try {
      navigator.vibrate(10)
    } catch {
      // 忽略不支持的设备
    }
  }
}

// 判断 tab 是否激活
function isActive(key: string): boolean {
  return activeKey.value === key
}
</script>

<template>
  <nav class="mobile-tabbar">
    <div class="tabbar-container">
      <button
        v-for="item in tabItems"
        :key="item.key"
        class="tabbar-item"
        :class="{ active: isActive(item.key) }"
        @click="handleTabClick(item.key)"
      >
        <!-- 图标 -->
        <div class="tabbar-icon">
          <NIcon :size="24" :component="isActive(item.key) ? item.iconActive : item.icon" />
          <!-- 徽章 -->
          <span
            v-if="item.badge"
            class="tabbar-badge"
            :class="{ animate: animateBadge }"
          >
            {{ typeof item.badge === 'number' && item.badge > 99 ? '99+' : item.badge }}
          </span>
        </div>
        <!-- 标签文字 -->
        <span class="tabbar-label">{{ item.label }}</span>
      </button>
    </div>

    <!-- 底部安全区域填充 -->
    <div class="tabbar-safe-area" />
  </nav>
</template>

<style scoped>
.mobile-tabbar {
  position: fixed;
  bottom: 0;
  left: 0;
  right: 0;
  z-index: var(--z-tabbar, 100);
  background: var(--glass-bg-thick, rgba(255, 255, 255, 0.85));
  backdrop-filter: saturate(180%) blur(20px);
  -webkit-backdrop-filter: saturate(180%) blur(20px);
  border-top: 0.5px solid var(--border-color, rgba(60, 60, 67, 0.12));
}

:global(.dark) .mobile-tabbar {
  background: var(--glass-bg-thick, rgba(30, 30, 30, 0.85));
  border-top-color: rgba(84, 84, 88, 0.65);
}

.tabbar-container {
  display: flex;
  justify-content: space-around;
  align-items: center;
  height: var(--tabbar-height, 56px);
  padding: 0 8px;
  max-width: 600px;
  margin: 0 auto;
}

.tabbar-item {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  flex: 1;
  min-width: 0;
  height: 100%;
  padding: 6px 4px;
  background: none;
  border: none;
  cursor: pointer;
  -webkit-tap-highlight-color: transparent;
  transition: transform 0.15s ease;
  color: var(--color-text-tertiary, #8E8E93);
}

.tabbar-item:active {
  transform: scale(0.92);
}

.tabbar-item.active {
  color: var(--color-primary, #007AFF);
}

.tabbar-icon {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  margin-bottom: 2px;
}

.tabbar-label {
  font-size: 10px;
  font-weight: 500;
  line-height: 1.2;
  text-align: center;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 100%;
}

.tabbar-item.active .tabbar-label {
  font-weight: 600;
}

/* 徽章样式 */
.tabbar-badge {
  position: absolute;
  top: -4px;
  right: -8px;
  min-width: 16px;
  height: 16px;
  padding: 0 4px;
  font-size: 10px;
  font-weight: 600;
  line-height: 16px;
  text-align: center;
  color: #fff;
  background: var(--color-error, #FF3B30);
  border-radius: 8px;
  box-shadow: 0 1px 3px rgba(255, 59, 48, 0.4);
}

.tabbar-badge.animate {
  animation: badge-bounce 0.3s ease;
}

@keyframes badge-bounce {
  0%, 100% {
    transform: scale(1);
  }
  50% {
    transform: scale(1.2);
  }
}

/* 底部安全区域 */
.tabbar-safe-area {
  height: env(safe-area-inset-bottom, 0);
  background: inherit;
}

/* 减少动画 */
@media (prefers-reduced-motion: reduce) {
  .tabbar-item {
    transition: none;
  }

  .tabbar-item:active {
    transform: none;
  }

  .tabbar-badge.animate {
    animation: none;
  }
}

/* 横屏时压缩高度 */
@media (orientation: landscape) and (max-height: 500px) {
  .tabbar-container {
    height: 44px;
  }

  .tabbar-icon {
    width: 24px;
    height: 24px;
  }

  .tabbar-label {
    font-size: 9px;
  }
}
</style>
