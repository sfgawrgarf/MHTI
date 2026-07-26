<script setup lang="ts">
import { ref, watch, onMounted, onUnmounted, nextTick } from 'vue'
import { NIcon, NButton, NAvatar, NDivider } from 'naive-ui'
import {
  CloseOutline,
  SunnyOutline,
  MoonOutline,
  PersonOutline,
} from '@vicons/ionicons5'
import { useTheme } from '@/composables/useTheme'
import { useAuthStore } from '@/stores/auth'

/**
 * MobileDrawer 组件
 * iOS 风格移动端侧边抽屉，支持手势滑动关闭
 */

const props = withDefaults(defineProps<{
  /** 是否显示抽屉 */
  show: boolean
  /** 抽屉位置 */
  placement?: 'left' | 'right'
  /** 抽屉宽度 */
  width?: string
  /** 是否显示遮罩 */
  showMask?: boolean
  /** 是否支持滑动关闭 */
  swipeToClose?: boolean
}>(), {
  placement: 'left',
  width: '280px',
  showMask: true,
  swipeToClose: true,
})

const emit = defineEmits<{
  'update:show': [value: boolean]
  close: []
}>()

const { isDark, toggleTheme } = useTheme()
const authStore = useAuthStore()

// 滑动状态
const drawerRef = ref<HTMLElement | null>(null)
const translateX = ref(0)
const isDragging = ref(false)

let touchStartX = 0
let touchStartY = 0

// 关闭抽屉
function close() {
  emit('update:show', false)
  emit('close')
}

// 触摸开始
function handleTouchStart(event: TouchEvent) {
  if (!props.swipeToClose) return

  const touch = event.touches[0]
  if (!touch) return
  touchStartX = touch.clientX
  touchStartY = touch.clientY
  isDragging.value = false
}

// 触摸移动
function handleTouchMove(event: TouchEvent) {
  if (!props.swipeToClose) return

  const touch = event.touches[0]
  if (!touch) return
  const deltaX = touch.clientX - touchStartX
  const deltaY = touch.clientY - touchStartY

  // 判断是否为水平滑动
  if (!isDragging.value && Math.abs(deltaX) > Math.abs(deltaY) && Math.abs(deltaX) > 10) {
    isDragging.value = true
  }

  if (isDragging.value) {
    event.preventDefault()

    // 根据方向限制滑动范围
    if (props.placement === 'left') {
      translateX.value = Math.min(0, Math.max(-parseInt(props.width), deltaX))
    } else {
      translateX.value = Math.max(0, Math.min(parseInt(props.width), deltaX))
    }
  }
}

// 触摸结束
function handleTouchEnd() {
  if (!isDragging.value) return

  const threshold = parseInt(props.width) * 0.3

  // 判断是否达到关闭阈值
  if (props.placement === 'left') {
    if (translateX.value < -threshold) {
      close()
    }
  } else {
    if (translateX.value > threshold) {
      close()
    }
  }

  // 重置位置
  translateX.value = 0
  isDragging.value = false
}

// 点击遮罩关闭
function handleMaskClick() {
  close()
}

// ESC 键关闭
function handleKeyDown(event: KeyboardEvent) {
  if (event.key === 'Escape' && props.show) {
    close()
  }
}

// 锁定背景滚动
function lockBodyScroll(lock: boolean) {
  if (lock) {
    document.body.style.overflow = 'hidden'
    document.body.style.touchAction = 'none'
  } else {
    document.body.style.overflow = ''
    document.body.style.touchAction = ''
  }
}

// 监听显示状态
watch(() => props.show, (show) => {
  lockBodyScroll(show)
  if (show) {
    nextTick(() => {
      drawerRef.value?.focus()
    })
  }
})

onMounted(() => {
  document.addEventListener('keydown', handleKeyDown)
})

onUnmounted(() => {
  document.removeEventListener('keydown', handleKeyDown)
  lockBodyScroll(false)
})

// 用户头像
const avatarSrc = authStore.avatar
  ? authStore.avatar.startsWith('data:')
    ? authStore.avatar
    : `data:image/png;base64,${authStore.avatar}`
  : undefined
</script>

<template>
  <Teleport to="body">
    <Transition name="drawer-fade">
      <div
        v-if="show"
        class="mobile-drawer-wrapper"
        :class="{ 'drawer-left': placement === 'left', 'drawer-right': placement === 'right' }"
      >
        <!-- 遮罩层 -->
        <div
          v-if="showMask"
          class="drawer-mask"
          @click="handleMaskClick"
        />

        <!-- 抽屉内容 -->
        <div
          ref="drawerRef"
          class="drawer-content"
          :style="{
            width,
            transform: isDragging ? `translateX(${translateX}px)` : undefined,
            transition: isDragging ? 'none' : undefined,
          }"
          tabindex="-1"
          @touchstart="handleTouchStart"
          @touchmove="handleTouchMove"
          @touchend="handleTouchEnd"
        >
          <!-- 抽屉头部 -->
          <div class="drawer-header">
            <div class="drawer-title">
              <slot name="title">菜单</slot>
            </div>
            <NButton quaternary circle size="small" @click="close">
              <template #icon>
                <NIcon :component="CloseOutline" />
              </template>
            </NButton>
          </div>

          <!-- 用户信息 -->
          <div class="drawer-user">
            <NAvatar :size="48" round :src="avatarSrc" class="user-avatar">
              <NIcon v-if="!avatarSrc" :component="PersonOutline" :size="24" />
            </NAvatar>
            <div class="user-info">
              <div class="user-name">{{ authStore.username || '管理员' }}</div>
              <div class="user-role">Administrator</div>
            </div>
          </div>

          <NDivider class="drawer-divider" />

          <!-- 抽屉主体内容 -->
          <div class="drawer-body">
            <slot />
          </div>

          <!-- 抽屉底部 -->
          <div class="drawer-footer">
            <NDivider class="drawer-divider" />

            <!-- 主题切换 -->
            <NButton
              quaternary
              block
              class="theme-toggle-btn"
              @click="toggleTheme"
            >
              <template #icon>
                <NIcon :component="isDark ? SunnyOutline : MoonOutline" />
              </template>
              {{ isDark ? '切换到亮色模式' : '切换到暗色模式' }}
            </NButton>

            <slot name="footer" />
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.mobile-drawer-wrapper {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  z-index: var(--z-drawer, 1000);
  display: flex;
}

.mobile-drawer-wrapper.drawer-right {
  justify-content: flex-end;
}

/* 遮罩层 */
.drawer-mask {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(0, 0, 0, 0.4);
  backdrop-filter: blur(2px);
  -webkit-backdrop-filter: blur(2px);
}

/* 抽屉内容 */
.drawer-content {
  position: relative;
  display: flex;
  flex-direction: column;
  height: 100%;
  background: var(--color-bg-elevated, #fff);
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.2);
  outline: none;
  overflow: hidden;
}

:global(.dark) .drawer-content {
  background: var(--color-bg-elevated, #1c1c1e);
}

.drawer-left .drawer-content {
  border-radius: 0 20px 20px 0;
}

.drawer-right .drawer-content {
  border-radius: 20px 0 0 20px;
}

/* 抽屉头部 */
.drawer-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 16px 12px;
  padding-top: calc(16px + env(safe-area-inset-top, 0));
}

.drawer-title {
  font-size: 17px;
  font-weight: 600;
  color: var(--color-text-primary, #1c1c1e);
}

:global(.dark) .drawer-title {
  color: #fff;
}

/* 用户信息 */
.drawer-user {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 16px;
}

.user-avatar {
  flex-shrink: 0;
  background: var(--color-primary, #007aff);
  box-shadow: 0 2px 8px rgba(0, 122, 255, 0.3);
}

.user-info {
  flex: 1;
  min-width: 0;
}

.user-name {
  font-size: 16px;
  font-weight: 600;
  color: var(--color-text-primary, #1c1c1e);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

:global(.dark) .user-name {
  color: #fff;
}

.user-role {
  font-size: 13px;
  color: var(--color-text-secondary, #8e8e93);
  margin-top: 2px;
}

.drawer-divider {
  margin: 8px 16px !important;
}

/* 抽屉主体 */
.drawer-body {
  flex: 1;
  padding: 8px 16px;
  overflow-y: auto;
  -webkit-overflow-scrolling: touch;
}

/* 抽屉底部 */
.drawer-footer {
  padding: 8px 16px 16px;
  padding-bottom: calc(16px + env(safe-area-inset-bottom, 0));
}

.theme-toggle-btn {
  justify-content: flex-start;
  height: 48px;
  border-radius: 12px;
  font-size: 15px;
  font-weight: 500;
}

.theme-toggle-btn :deep(.n-button__icon) {
  margin-right: 12px;
}

/* 动画 */
.drawer-fade-enter-active,
.drawer-fade-leave-active {
  transition: opacity 0.3s ease;
}

.drawer-fade-enter-active .drawer-content,
.drawer-fade-leave-active .drawer-content {
  transition: transform 0.3s cubic-bezier(0.32, 0.72, 0, 1);
}

.drawer-fade-enter-from,
.drawer-fade-leave-to {
  opacity: 0;
}

.drawer-left.drawer-fade-enter-from .drawer-content,
.drawer-left.drawer-fade-leave-to .drawer-content {
  transform: translateX(-100%);
}

.drawer-right.drawer-fade-enter-from .drawer-content,
.drawer-right.drawer-fade-leave-to .drawer-content {
  transform: translateX(100%);
}

/* 减少动画 */
@media (prefers-reduced-motion: reduce) {
  .drawer-fade-enter-active,
  .drawer-fade-leave-active,
  .drawer-fade-enter-active .drawer-content,
  .drawer-fade-leave-active .drawer-content {
    transition: none;
  }
}
</style>
