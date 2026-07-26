<script setup lang="ts">
import { ref, computed } from 'vue'
import { NCard } from 'naive-ui'
import { useMobileLayout } from '@/composables/useMobileLayout'

/**
 * TouchCard 组件
 * 增强的卡片组件，支持触摸反馈、滑动操作和 iOS 风格交互
 */

const props = withDefaults(defineProps<{
  /** 是否启用触摸反馈 */
  touchFeedback?: boolean
  /** 是否可点击 */
  clickable?: boolean
  /** 是否启用滑动操作 */
  swipeable?: boolean
  /** 左滑阈值（像素） */
  swipeThreshold?: number
  /** 卡片尺寸 */
  size?: 'small' | 'medium' | 'large'
  /** 是否使用玻璃态效果 */
  glass?: boolean
  /** 是否禁用 */
  disabled?: boolean
}>(), {
  touchFeedback: true,
  clickable: false,
  swipeable: false,
  swipeThreshold: 80,
  size: 'medium',
  glass: false,
  disabled: false,
})

const emit = defineEmits<{
  click: [event: MouseEvent | TouchEvent]
  swipeLeft: []
  swipeRight: []
  longPress: []
}>()

const { isTouchDevice } = useMobileLayout()

// 触摸状态
const isPressed = ref(false)
const isSwiping = ref(false)
const swipeOffset = ref(0)
const longPressTimer = ref<ReturnType<typeof setTimeout> | null>(null)

// 触摸起始位置
let touchStartX = 0
let touchStartY = 0
let touchStartTime = 0

// 卡片类名
const cardClass = computed(() => ({
  'touch-card': true,
  'touch-card--pressed': isPressed.value && props.touchFeedback,
  'touch-card--clickable': props.clickable,
  'touch-card--swiping': isSwiping.value,
  'touch-card--glass': props.glass,
  'touch-card--disabled': props.disabled,
  [`touch-card--${props.size}`]: true,
}))

// 滑动样式
const swipeStyle = computed(() => {
  if (!isSwiping.value || swipeOffset.value === 0) return {}
  return {
    transform: `translateX(${swipeOffset.value}px)`,
    transition: isSwiping.value ? 'none' : 'transform 0.3s ease-out',
  }
})

// 触摸开始
function handleTouchStart(event: TouchEvent) {
  if (props.disabled) return

  const touch = event.touches[0]
  if (!touch) return
  touchStartX = touch.clientX
  touchStartY = touch.clientY
  touchStartTime = Date.now()

  // 触摸反馈
  if (props.touchFeedback) {
    isPressed.value = true
  }

  // 长按检测
  longPressTimer.value = setTimeout(() => {
    if (isPressed.value && !isSwiping.value) {
      emit('longPress')
      // 触发触觉反馈（如果支持）
      triggerHaptic('medium')
    }
  }, 500)
}

// 触摸移动
function handleTouchMove(event: TouchEvent) {
  if (props.disabled) return

  const touch = event.touches[0]
  if (!touch) return
  const deltaX = touch.clientX - touchStartX
  const deltaY = touch.clientY - touchStartY

  // 判断是否为水平滑动
  if (props.swipeable && Math.abs(deltaX) > Math.abs(deltaY) && Math.abs(deltaX) > 10) {
    isSwiping.value = true
    swipeOffset.value = deltaX

    // 取消长按
    if (longPressTimer.value) {
      clearTimeout(longPressTimer.value)
      longPressTimer.value = null
    }

    // 阻止垂直滚动
    event.preventDefault()
  }
}

// 检查是否为交互元素（按钮、链接、输入框等）
function isInteractiveElement(element: HTMLElement | null): boolean {
  if (!element) return false

  const interactiveTags = ['BUTTON', 'A', 'INPUT', 'TEXTAREA', 'SELECT', 'LABEL']
  const interactiveRoles = ['button', 'link', 'checkbox', 'radio', 'switch', 'tab']

  let current: HTMLElement | null = element

  // 向上遍历 DOM 树，检查是否在交互元素内
  while (current && current !== document.body) {
    // 检查标签名
    if (interactiveTags.includes(current.tagName)) {
      return true
    }
    // 检查 role 属性
    const role = current.getAttribute('role')
    if (role && interactiveRoles.includes(role)) {
      return true
    }
    // 检查 Naive UI 按钮类名
    if (current.classList.contains('n-button')) {
      return true
    }
    current = current.parentElement
  }

  return false
}

// 触摸结束
function handleTouchEnd(event: TouchEvent) {
  if (props.disabled) return

  // 清理长按计时器
  if (longPressTimer.value) {
    clearTimeout(longPressTimer.value)
    longPressTimer.value = null
  }

  // 处理滑动完成
  if (isSwiping.value) {
    if (Math.abs(swipeOffset.value) > props.swipeThreshold) {
      if (swipeOffset.value < 0) {
        emit('swipeLeft')
        triggerHaptic('light')
      } else {
        emit('swipeRight')
        triggerHaptic('light')
      }
    }
    // 重置滑动状态
    isSwiping.value = false
    swipeOffset.value = 0
  } else if (isPressed.value && props.clickable) {
    // 判断是否为点击（短时间内没有明显移动）
    const touchDuration = Date.now() - touchStartTime
    if (touchDuration < 300) {
      // 检查点击目标是否为交互元素，如果是则不触发卡片点击
      const target = event.target as HTMLElement
      if (!isInteractiveElement(target)) {
        emit('click', event)
        triggerHaptic('light')
      }
    }
  }

  isPressed.value = false
}

// 触摸取消
function handleTouchCancel() {
  if (longPressTimer.value) {
    clearTimeout(longPressTimer.value)
    longPressTimer.value = null
  }
  isPressed.value = false
  isSwiping.value = false
  swipeOffset.value = 0
}

// 鼠标点击（非触摸设备）
function handleClick(event: MouseEvent) {
  if (props.disabled || isTouchDevice.value) return
  if (props.clickable) {
    // 检查点击目标是否为交互元素，如果是则不触发卡片点击
    const target = event.target as HTMLElement
    if (!isInteractiveElement(target)) {
      emit('click', event)
    }
  }
}

// 鼠标按下（非触摸设备的视觉反馈）
function handleMouseDown() {
  if (props.disabled || isTouchDevice.value) return
  if (props.touchFeedback && props.clickable) {
    isPressed.value = true
  }
}

function handleMouseUp() {
  isPressed.value = false
}

function handleMouseLeave() {
  isPressed.value = false
}

// 触发触觉反馈
function triggerHaptic(intensity: 'light' | 'medium' | 'heavy' = 'light') {
  if (!('vibrate' in navigator)) return

  const durations = {
    light: 10,
    medium: 20,
    heavy: 30,
  }

  try {
    navigator.vibrate(durations[intensity])
  } catch {
    // 忽略不支持的设备
  }
}
</script>

<template>
  <NCard
    :class="cardClass"
    :style="swipeStyle"
    :bordered="!glass"
    @touchstart="handleTouchStart"
    @touchmove="handleTouchMove"
    @touchend="handleTouchEnd"
    @touchcancel="handleTouchCancel"
    @click="handleClick"
    @mousedown="handleMouseDown"
    @mouseup="handleMouseUp"
    @mouseleave="handleMouseLeave"
  >
    <!-- 滑动操作按钮区域（左侧） -->
    <div v-if="swipeable && swipeOffset > 0" class="swipe-actions swipe-actions--left">
      <slot name="swipe-left">
        <div class="swipe-action swipe-action--secondary">
          <span>操作</span>
        </div>
      </slot>
    </div>

    <!-- 卡片内容 -->
    <div class="touch-card__content">
      <slot />
    </div>

    <!-- 滑动操作按钮区域（右侧） -->
    <div v-if="swipeable && swipeOffset < 0" class="swipe-actions swipe-actions--right">
      <slot name="swipe-right">
        <div class="swipe-action swipe-action--danger">
          <span>删除</span>
        </div>
      </slot>
    </div>

    <!-- 头部插槽 -->
    <template v-if="$slots.header" #header>
      <slot name="header" />
    </template>

    <!-- 头部额外内容插槽 -->
    <template v-if="$slots['header-extra']" #header-extra>
      <slot name="header-extra" />
    </template>

    <!-- 底部插槽 -->
    <template v-if="$slots.footer" #footer>
      <slot name="footer" />
    </template>

    <!-- 动作区插槽 -->
    <template v-if="$slots.action" #action>
      <slot name="action" />
    </template>
  </NCard>
</template>

<style scoped>
.touch-card {
  position: relative;
  overflow: visible;
  transition:
    transform 0.15s cubic-bezier(0.25, 0.46, 0.45, 0.94),
    box-shadow 0.15s ease,
    background-color 0.15s ease;
  -webkit-tap-highlight-color: transparent;
  user-select: none;
}

/* 尺寸变体 */
.touch-card--small {
  --n-padding-top: 12px;
  --n-padding-bottom: 12px;
  --n-padding-left: 12px;
}

.touch-card--medium {
  --n-padding-top: 16px;
  --n-padding-bottom: 16px;
  --n-padding-left: 16px;
}

.touch-card--large {
  --n-padding-top: 20px;
  --n-padding-bottom: 20px;
  --n-padding-left: 20px;
}

/* 可点击状态 */
.touch-card--clickable {
  cursor: pointer;
}

.touch-card--clickable:hover:not(.touch-card--disabled) {
  box-shadow: var(--shadow-md, 0 4px 12px rgba(0, 0, 0, 0.1));
}

/* 按压状态 */
.touch-card--pressed {
  transform: scale(0.98);
  opacity: 0.9;
}

/* 玻璃态效果 */
.touch-card--glass {
  background: var(--glass-bg-thick, rgba(255, 255, 255, 0.72));
  backdrop-filter: blur(20px);
  -webkit-backdrop-filter: blur(20px);
  border: 1px solid var(--glass-border, rgba(255, 255, 255, 0.2));
}

:global(.dark) .touch-card--glass {
  background: var(--glass-bg-thick, rgba(30, 30, 30, 0.72));
}

/* 禁用状态 */
.touch-card--disabled {
  opacity: 0.5;
  pointer-events: none;
}

/* 滑动状态 */
.touch-card--swiping {
  z-index: 10;
}

/* 卡片内容 */
.touch-card__content {
  position: relative;
  z-index: 1;
}

/* 滑动操作区域 */
.swipe-actions {
  position: absolute;
  top: 0;
  bottom: 0;
  display: flex;
  align-items: center;
  z-index: 0;
}

.swipe-actions--left {
  right: 100%;
  padding-right: 8px;
}

.swipe-actions--right {
  left: 100%;
  padding-left: 8px;
}

.swipe-action {
  display: flex;
  align-items: center;
  justify-content: center;
  min-width: 72px;
  height: 100%;
  padding: 0 16px;
  border-radius: var(--radius-md, 8px);
  font-size: 14px;
  font-weight: 500;
  color: #fff;
}

.swipe-action--danger {
  background: var(--color-error, #ff3b30);
}

.swipe-action--secondary {
  background: var(--color-secondary, #8e8e93);
}

.swipe-action--primary {
  background: var(--color-primary, #007aff);
}

/* 减少动画 */
@media (prefers-reduced-motion: reduce) {
  .touch-card {
    transition: none;
  }

  .touch-card--pressed {
    transform: none;
  }
}
</style>
