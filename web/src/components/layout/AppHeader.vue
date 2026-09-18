<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { NIcon, NButton, NTooltip, NText } from 'naive-ui'
import { RefreshOutline, MenuOutline } from '@vicons/ionicons5'

/**
 * AppHeader - 顶部导航栏
 * 支持移动端菜单按钮
 */

withDefaults(defineProps<{
  /** 是否显示菜单按钮（移动端） */
  showMenuButton?: boolean
}>(), {
  showMenuButton: false,
})

const emit = defineEmits<{
  'menu-click': []
}>()

const route = useRoute()

const pageInfo = computed(() => {
  const pages: Record<string, { title: string; subtitle: string }> = {
    '/': { title: '主界面', subtitle: '查看任务统计和快捷操作' },
    '/scan': { title: '手动任务', subtitle: '创建和管理刮削任务' },
    '/settings': { title: '设置', subtitle: '配置应用参数' },
    '/scheduler': { title: '定时任务', subtitle: '管理自动化任务' },
    '/history': { title: '刮削记录', subtitle: '查看历史刮削结果' },
    '/watcher': { title: '文件夹监控', subtitle: '监控文件夹变化' },
    '/files': { title: '文件管理', subtitle: '浏览和管理媒体文件' },
    '/security': { title: '安全设置', subtitle: '管理账户和访问权限' },
  }
  return pages[route.path] || { title: '', subtitle: '' }
})

const handleRefresh = () => {
  window.location.reload()
}

const handleMenuClick = () => {
  emit('menu-click')
}
</script>

<template>
  <header class="app-header" :class="{ 'has-menu-btn': showMenuButton }">
    <div class="header-left">
      <!-- 移动端菜单按钮 -->
      <NButton
        v-if="showMenuButton"
        quaternary
        circle
        class="menu-btn"
        @click="handleMenuClick"
      >
        <template #icon>
          <NIcon :component="MenuOutline" :size="22" />
        </template>
      </NButton>

      <div class="page-info">
        <h1 class="page-title">{{ pageInfo.title }}</h1>
        <NText v-if="!showMenuButton" class="page-subtitle" depth="3">{{ pageInfo.subtitle }}</NText>
      </div>
    </div>
    <div class="header-right">
      <NTooltip trigger="hover">
        <template #trigger>
          <NButton quaternary circle class="refresh-btn" @click="handleRefresh">
            <template #icon>
              <NIcon :component="RefreshOutline" :size="20" />
            </template>
          </NButton>
        </template>
        刷新页面
      </NTooltip>
    </div>
  </header>
</template>

<style scoped>
.app-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: var(--header-height, 64px);
  padding: 0 24px;
}

.app-header.has-menu-btn {
  padding: 0 16px;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 12px;
}

.menu-btn {
  width: 44px;
  height: 44px;
  border-radius: 12px;
  color: var(--color-text-primary, #1c1c1e);
  transition: all 0.2s ease;
  flex-shrink: 0;
}

:global(.dark) .menu-btn {
  color: #fff;
}

.menu-btn:hover {
  background: rgba(0, 122, 255, 0.1);
  color: var(--color-primary, #007aff);
}

.menu-btn:active {
  transform: scale(0.95);
}

.page-info {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.page-title {
  margin: 0;
  font-size: 20px;
  font-weight: 700;
  line-height: 1.3;
  color: var(--color-text-primary, #1c1c1e);
  letter-spacing: -0.5px;
}

:global(.dark) .page-title {
  color: #fff;
}

.has-menu-btn .page-title {
  font-size: 17px;
}

.page-subtitle {
  font-size: 13px;
  line-height: 1.4;
  color: var(--color-text-secondary, #8e8e93) !important;
}

.header-right {
  display: flex;
  align-items: center;
  gap: 8px;
}

.refresh-btn {
  width: 40px;
  height: 40px;
  border-radius: 12px;
  color: var(--color-text-secondary, #8e8e93);
  transition: all 0.2s ease;
}

.refresh-btn:hover {
  background: rgba(0, 122, 255, 0.1);
  color: var(--color-primary, #007aff);
  transform: rotate(90deg);
}

/* 移动端优化 */
@media (max-width: 768px) {
  .app-header {
    padding: 0 12px;
  }

  .page-title {
    font-size: 17px;
  }

  .page-subtitle {
    display: none;
  }

  .refresh-btn {
    width: 36px;
    height: 36px;
  }
}
</style>
