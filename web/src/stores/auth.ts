import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { useWebSocket } from '@/shared/composables/useWebSocket'
import { createTokenManager } from '@/stores/authTokens'
import {
  authApi,
  type LoginRequest,
  type RegisterRequest,
  type ExpireOption,
  type SessionInfo,
  type LoginHistoryItem,
  type UserProfile,
  type ChangePasswordRequest,
  type UpdateUsernameRequest,
} from '@/modules/auth'

export const useAuthStore = defineStore('auth', () => {
  // 状态
  const isAuthenticated = ref(false)
  const username = ref<string | null>(null)
  const sessionId = ref<string | null>(null)
  const isInitialized = ref<boolean | null>(null)
  const isReady = ref(false)
  const expiresAt = ref<number | null>(null)
  const avatar = ref<string | null>(null)

  // 计算属性
  const shouldRefresh = computed(() => {
    if (!expiresAt.value) return false
    // 在过期前 1 分钟刷新
    return Date.now() > expiresAt.value - 60 * 1000
  })

  // Token 生命周期（localStorage + 自动刷新，见 authTokens.ts）
  const {
    getAccessToken,
    getRefreshToken,
    setTokens,
    clearTokens,
    setupAutoRefresh,
    refreshAccessToken,
    getStoredExpiresAt,
    getStoredSessionId,
  } = createTokenManager({ expiresAt, sessionId, isAuthenticated, username })

  // 检查初始化状态
  async function checkInitialized(): Promise<boolean> {
    try {
      const response = await authApi.status()
      isInitialized.value = response.data.initialized
      return response.data.initialized
    } catch {
      isInitialized.value = false
      return false
    }
  }

  // 注册
  async function register(data: RegisterRequest): Promise<void> {
    const response = await authApi.register(data)
    setTokens(
      response.data.access_token,
      response.data.refresh_token,
      response.data.session_id,
      response.data.expires_in
    )
    isAuthenticated.value = true
    username.value = data.username
    isInitialized.value = true
    // 登录成功后建立实时通道（token 已写入，WS 服务端要求鉴权）
    useWebSocket().connect()
  }

  // 登录
  async function login(
    user: string,
    password: string,
    expireOption: ExpireOption = '7d',
    deviceName?: string
  ): Promise<void> {
    const data: LoginRequest = {
      username: user,
      password,
      expire_option: expireOption,
      device_name: deviceName,
    }
    const response = await authApi.login(data)
    setTokens(
      response.data.access_token,
      response.data.refresh_token,
      response.data.session_id,
      response.data.expires_in
    )
    isAuthenticated.value = true
    username.value = user
    // 注册成功后建立实时通道
    useWebSocket().connect()
  }

  // 检查认证状态
  async function checkAuth(allowRefresh = true): Promise<boolean> {
    const token = getAccessToken()
    const refreshToken = getRefreshToken()
    const storedExpiresAt = getStoredExpiresAt()

    console.log('[Auth] checkAuth 开始', {
      hasAccessToken: !!token,
      hasRefreshToken: !!refreshToken,
      storedExpiresAt,
    })

    if (!token) {
      console.log('[Auth] 没有 Access Token，未登录')
      isAuthenticated.value = false
      username.value = null
      isReady.value = true
      return false
    }

    // 恢复过期时间
    if (storedExpiresAt) {
      expiresAt.value = storedExpiresAt
    }

    // 恢复 session ID
    sessionId.value = getStoredSessionId()

    // 检查是否需要刷新
    if (shouldRefresh.value) {
      console.log('[Auth] Token 接近过期或已过期，尝试刷新')
      const refreshed = await refreshAccessToken()
      if (!refreshed) {
        console.log('[Auth] 刷新失败')
        isReady.value = true
        return false
      }
      console.log('[Auth] 刷新成功')
    }

    try {
      console.log('[Auth] 验证 Token')
      const response = await authApi.verify()
      isAuthenticated.value = response.data.valid
      username.value = response.data.username
      sessionId.value = response.data.session_id
      isReady.value = true

      // 已有登录态时（如刷新页面后恢复）建立实时通道
      if (isAuthenticated.value) {
        useWebSocket().connect()
      }

      // 设置自动刷新
      if (expiresAt.value) {
        const remainingSeconds = Math.floor((expiresAt.value - Date.now()) / 1000)
        if (remainingSeconds > 0) {
          setupAutoRefresh(remainingSeconds)
        }
      }

      console.log('[Auth] 验证成功', { username: response.data.username })
      return response.data.valid
    } catch (error) {
      console.log('[Auth] 验证失败，尝试刷新', error)
      // 验证失败，尝试刷新
      const status = (error as { response?: { status?: number } }).response?.status
      const refreshed = allowRefresh && status === 401 && await refreshAccessToken()
      if (refreshed) {
        return checkAuth(false)
      }
      if (status !== 401) {
        // A service/network error does not invalidate stored credentials.
        isAuthenticated.value = false
        isReady.value = true
        return false
      }

      console.log('[Auth] 刷新也失败，清除登录状态')
      clearTokens()
      isAuthenticated.value = false
      username.value = null
      isReady.value = true
      return false
    }
  }

  // 登出
  async function logout() {
    try {
      await authApi.logout()
    } catch {
      // 忽略登出错误
    }
    clearTokens()
    isAuthenticated.value = false
    username.value = null
  }

  // 获取会话列表
  async function getSessions(): Promise<SessionInfo[]> {
    const response = await authApi.getSessions()
    return response.data.sessions
  }

  // 注销指定会话
  async function revokeSession(sid: string): Promise<void> {
    await authApi.revokeSession(sid)
  }

  // 注销所有其他会话
  async function revokeAllSessions(): Promise<void> {
    await authApi.revokeAllSessions()
  }

  // 获取登录历史
  async function getLoginHistory(
    limit = 20,
    offset = 0
  ): Promise<{ items: LoginHistoryItem[]; total: number }> {
    const response = await authApi.getHistory(limit, offset)
    return response.data
  }

  // ========== 账户管理方法 ==========

  // 获取用户资料
  async function getProfile(): Promise<UserProfile> {
    const response = await authApi.getProfile()
    avatar.value = response.data.avatar
    return response.data
  }

  // 修改密码
  async function changePassword(data: ChangePasswordRequest): Promise<{ success: boolean; message: string }> {
    const response = await authApi.changePassword(data)
    return response.data
  }

  // 修改用户名
  async function updateUsername(data: UpdateUsernameRequest): Promise<{ success: boolean; message: string }> {
    const response = await authApi.updateUsername(data)
    if (response.data.success && response.data.new_username) {
      username.value = response.data.new_username
    }
    return response.data
  }

  // 更新头像
  async function updateAvatar(avatarData: string): Promise<{ success: boolean; message: string }> {
    const response = await authApi.updateAvatar({ avatar: avatarData })
    if (response.data.success) {
      avatar.value = avatarData
    }
    return response.data
  }

  // 删除头像
  async function deleteAvatar(): Promise<{ success: boolean; message: string }> {
    const response = await authApi.deleteAvatar()
    if (response.data.success) {
      avatar.value = null
    }
    return response.data
  }

  return {
    // 状态
    isAuthenticated,
    username,
    sessionId,
    isInitialized,
    isReady,
    expiresAt,
    avatar,

    // 方法
    getAccessToken,
    getRefreshToken,
    checkInitialized,
    register,
    login,
    logout,
    checkAuth,
    refreshAccessToken,

    // 会话管理
    getSessions,
    revokeSession,
    revokeAllSessions,
    getLoginHistory,

    // 账户管理
    getProfile,
    changePassword,
    updateUsername,
    updateAvatar,
    deleteAvatar,
  }
})
