import { ref, type Ref } from 'vue'

/**
 * 触摸手势 composable
 *
 * 从 TouchCard 提取：按压反馈 / 点击判定（含"命中交互元素时不触发卡片点击"）
 * / 鼠标降级（非触摸设备）。
 *
 * 长按与水平滑动分支已随零消费的 TouchCard props（swipeable/longPress 等）
 * 一并删除，本文件只保留实际被消费的点击链路；入参用 getter 形式读取，
 * 避免 props 快照过期。
 */
export interface UseTouchGestureOptions {
  /** 是否可点击 */
  clickable: () => boolean
  /** 是否启用按压反馈 */
  touchFeedback: () => boolean
  /** 是否禁用 */
  disabled: () => boolean
  /** 是否触摸设备（鼠标降级门控） */
  isTouchDevice: Ref<boolean>
  /** 点击（已过滤交互元素命中） */
  onTap: (event: MouseEvent | TouchEvent) => void
}

/** 点击判定上限：按下到抬起不超过该时长才算点击 */
const TAP_DURATION = 300

export function useTouchGesture(options: UseTouchGestureOptions) {
  let lastTouchAt = Number.NEGATIVE_INFINITY
  // 触摸状态
  const isPressed = ref(false)

  // 触摸起始时间
  let touchStartTime = 0

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

  // 触摸开始
  function handleTouchStart(event: TouchEvent) {
    if (options.disabled()) return

    const touch = event.touches[0]
    if (!touch) return
    touchStartTime = Date.now()

    // 触摸反馈
    if (options.touchFeedback()) {
      isPressed.value = true
    }
  }

  // 触摸结束
  function handleTouchEnd(event: TouchEvent) {
    lastTouchAt = Date.now()
    if (options.disabled()) return

    if (isPressed.value && options.clickable()) {
      // 判断是否为点击（短时间内没有明显移动）
      const touchDuration = Date.now() - touchStartTime
      if (touchDuration < TAP_DURATION) {
        // 检查点击目标是否为交互元素，如果是则不触发卡片点击
        const target = event.target as HTMLElement
        if (!isInteractiveElement(target)) {
          options.onTap(event)
          triggerHaptic('light')
        }
      }
    }

    isPressed.value = false
  }

  // 触摸取消
  function handleTouchCancel() {
    isPressed.value = false
  }

  // 鼠标点击（非触摸设备）
  function handleClick(event: MouseEvent) {
    if (options.disabled()) return
    const pointer = event as MouseEvent & { pointerType?: string; sourceCapabilities?: { firesTouchEvents?: boolean } }
    if (pointer.pointerType === 'touch' || pointer.sourceCapabilities?.firesTouchEvents ||
        (!pointer.pointerType && event.detail !== 0 && Date.now() - lastTouchAt < 700)) return
    if (options.clickable()) {
      // 检查点击目标是否为交互元素，如果是则不触发卡片点击
      const target = event.target as HTMLElement
      if (!isInteractiveElement(target)) {
        options.onTap(event)
      }
    }
  }

  // 鼠标按下（非触摸设备的视觉反馈）
  function handleMouseDown() {
    if (options.disabled()) return
    if (options.touchFeedback() && options.clickable()) {
      isPressed.value = true
    }
  }

  function handleMouseUp() {
    isPressed.value = false
  }

  function handleMouseLeave() {
    isPressed.value = false
  }

  return {
    isPressed,
    handleTouchStart,
    handleTouchEnd,
    handleTouchCancel,
    handleClick,
    handleMouseDown,
    handleMouseUp,
    handleMouseLeave,
  }
}
