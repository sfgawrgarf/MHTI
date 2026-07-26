<script setup lang="ts">
import { h, computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NMenu, NIcon, NButton, NTooltip, NAvatar, NDivider } from 'naive-ui'
import type { MenuOption } from 'naive-ui'
import {
  HomeOutline,
  SearchOutline,
  ListOutline,
  FolderOutline,
  SettingsOutline,
  SunnyOutline,
  MoonOutline,
  PersonOutline,
} from '@vicons/ionicons5'
import { useTheme } from '@/composables/useTheme'
import { useAuthStore } from '@/stores/auth'
import AdminConfigDrawer from './AdminConfigDrawer.vue'

const props = defineProps<{
  collapsed?: boolean
}>()

const emit = defineEmits<{
  navigate: [path: string]
}>()

const route = useRoute()
const router = useRouter()
const { isDark, toggleTheme } = useTheme()
const authStore = useAuthStore()

// 管理员配置抽屉状态
const showAdminDrawer = ref(false)

const themeIcon = computed(() => (isDark.value ? SunnyOutline : MoonOutline))
const themeTooltip = computed(() => (isDark.value ? '切换到亮色模式' : '切换到暗色模式'))

// 用户头像
const avatarSrc = computed(() => {
  if (authStore.avatar) {
    if (authStore.avatar.startsWith('data:')) {
      return authStore.avatar
    }
    return `data:image/png;base64,${authStore.avatar}`
  }
  return undefined
})

const renderIcon = (icon: any) => () => h(NIcon, null, { default: () => h(icon) })

const menuOptions: MenuOption[] = [
  {
    label: '主界面',
    key: '/',
    icon: renderIcon(HomeOutline),
  },
  {
    label: '手动任务',
    key: '/scan',
    icon: renderIcon(SearchOutline),
  },
  {
    label: '刮削记录',
    key: '/history',
    icon: renderIcon(ListOutline),
  },
  {
    label: '文件管理',
    key: '/files',
    icon: renderIcon(FolderOutline),
  },
  {
    label: '设置',
    key: '/settings',
    icon: renderIcon(SettingsOutline),
  },
]

const activeKey = computed(() => route.path)

const handleMenuUpdate = (key: string) => {
  router.push(key)
  // 触发导航事件（用于移动端抽屉关闭）
  emit('navigate', key)
}

// 打开管理员配置抽屉
const openAdminDrawer = () => {
  showAdminDrawer.value = true
}
</script>

<template>
  <div class="sidebar-container" :class="{ collapsed }">
    <div class="sidebar-menu">
      <NMenu
        :value="activeKey"
        :options="menuOptions"
        :collapsed="collapsed"
        :collapsed-width="72"
        :collapsed-icon-size="22"
        :icon-size="20"
        :indent="20"
        :root-indent="16"
        @update:value="handleMenuUpdate"
      />
    </div>

    <!-- 底部用户区域 -->
    <div class="sidebar-footer" :class="{ collapsed }">
      <!-- 用户信息（可点击打开配置抽屉） -->
      <NTooltip trigger="hover" placement="right" :disabled="!collapsed">
        <template #trigger>
          <div class="user-section clickable" :class="{ collapsed }" @click="openAdminDrawer">
            <NAvatar
              :size="collapsed ? 40 : 36"
              round
              :src="avatarSrc"
              class="user-avatar"
            >
              <NIcon v-if="!avatarSrc" :component="PersonOutline" :size="collapsed ? 20 : 18" />
            </NAvatar>
            <span v-if="!collapsed" class="user-name">{{ authStore.username || '管理员' }}</span>
          </div>
        </template>
        点击管理账户
      </NTooltip>

      <NDivider class="footer-divider" />

      <!-- 主题切换 -->
      <NTooltip trigger="hover" placement="right" :disabled="!collapsed">
        <template #trigger>
          <NButton
            quaternary
            :circle="collapsed"
            class="theme-btn"
            :class="{ collapsed }"
            @click="toggleTheme"
          >
            <template #icon>
              <NIcon :component="themeIcon" :size="20" />
            </template>
            <span v-if="!collapsed" class="theme-text">{{ isDark ? '亮色模式' : '暗色模式' }}</span>
          </NButton>
        </template>
        {{ themeTooltip }}
      </NTooltip>
    </div>

    <!-- 管理员配置抽屉 -->
    <AdminConfigDrawer v-model:show="showAdminDrawer" />
  </div>
</template>

<style scoped>
.sidebar-container {
  display: flex;
  flex-direction: column;
  height: calc(100vh - 64px);
}

.sidebar-menu {
  flex: 1;
  padding: 12px;
  overflow-y: auto;
}

/* iOS 风格菜单项 */
.sidebar-menu :deep(.n-menu-item) {
  margin: 4px 0;
  border-radius: 12px;
  transition: all 0.2s ease;
}

.sidebar-menu :deep(.n-menu-item-content) {
  border-radius: 12px;
  height: 44px;
}

.sidebar-menu :deep(.n-menu-item-content--selected) {
  font-weight: 600;
}

.sidebar-menu :deep(.n-menu-item-content--selected::before) {
  border-radius: 12px;
  left: 0;
  right: 0;
}

/* 展开状态悬浮效果 */
.sidebar-container:not(.collapsed) .sidebar-menu :deep(.n-menu-item:hover) {
  transform: translateX(2px);
}

/* 折叠状态样式优化 */
.sidebar-container.collapsed .sidebar-menu {
  padding: 12px 12px;
}

.sidebar-container.collapsed .sidebar-menu :deep(.n-menu-item) {
  margin: 6px 0;
}

.sidebar-container.collapsed .sidebar-menu :deep(.n-menu-item-content) {
  width: 48px !important;
  height: 48px !important;
  padding: 0 !important;
  padding-left: 0 !important;
  margin: 0 auto;
  display: flex !important;
  align-items: center !important;
  justify-content: center !important;
  border-radius: 14px;
}

.sidebar-container.collapsed .sidebar-menu :deep(.n-menu-item-content:hover) {
  transform: scale(1.05);
}

.sidebar-container.collapsed .sidebar-menu :deep(.n-menu-item-content--selected) {
  background: rgba(0, 122, 255, 0.15) !important;
  box-shadow: 0 4px 12px rgba(0, 122, 255, 0.2);
}

.sidebar-container.collapsed .sidebar-menu :deep(.n-menu-item-content--selected::before) {
  display: none !important;
}

.sidebar-container.collapsed .sidebar-menu :deep(.n-menu-item-content__icon) {
  margin: 0 !important;
  margin-right: 0 !important;
}

.sidebar-container.collapsed .sidebar-menu :deep(.n-menu-item-content-header) {
  display: none !important;
}

/* 底部区域 */
.sidebar-footer {
  padding: 16px;
  border-top: 1px solid var(--ios-separator);
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.sidebar-footer.collapsed {
  padding: 16px 12px;
  align-items: center;
}

.footer-divider {
  margin: 8px 0 !important;
}

/* 用户信息区域 */
.user-section {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 8px 12px;
  border-radius: 12px;
  transition: all 0.2s ease;
}

.user-section.clickable {
  cursor: pointer;
}

.user-section.clickable:hover {
  background: var(--ios-blue-light);
}

.user-section.collapsed {
  justify-content: center;
  padding: 8px;
}

.user-section.collapsed.clickable:hover {
  transform: scale(1.05);
}

.user-avatar {
  background: var(--ios-blue);
  flex-shrink: 0;
  box-shadow: 0 2px 8px rgba(0, 122, 255, 0.25);
}

.user-name {
  font-size: 14px;
  font-weight: 500;
  color: var(--ios-text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 主题切换按钮 */
.theme-btn {
  width: 100%;
  justify-content: flex-start;
  padding: 0 16px;
  height: 44px;
  border-radius: 12px;
  transition: all 0.2s ease;
  color: var(--ios-text-primary);
}

.theme-btn.collapsed {
  width: 48px;
  height: 48px;
  padding: 0;
  justify-content: center;
  border-radius: 14px;
}

.theme-btn:hover {
  background: var(--ios-blue-light);
  color: var(--ios-blue);
}

.theme-btn.collapsed:hover {
  transform: scale(1.05);
}

.theme-btn :deep(.n-button__icon) {
  margin-right: 12px;
}

.theme-btn.collapsed :deep(.n-button__icon) {
  margin-right: 0;
}

.theme-text {
  font-size: 15px;
  font-weight: 500;
}
</style>
