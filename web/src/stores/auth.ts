import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
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
} from '@/api/auth'
import { clearStoredAuthTokens, refreshStoredAccessToken } from '@/api'

// Token 存储键
const ACCESS_TOKEN_KEY = 'access_token'
const REFRESH_TOKEN_KEY = 'refresh_token'
const SESSION_ID_KEY = 'session_id'
const EXPIRES_AT_KEY = 'expires_at'

export const useAuthStore = defineStore('auth', () => {
  // 状态
  const isAuthenticated = ref(false)
  const username = ref<string | null>(null)
  const sessionId = ref<string | null>(null)
  const isInitialized = ref<boolean | null>(null)
  const isReady = ref(false)
  const expiresAt = ref<number | null>(null)
  const avatar = ref<string | null>(null)

  // 刷新定时器
  let refreshTimer: ReturnType<typeof setTimeout> | null = null

  // 计算属性
  const shouldRefresh = computed(() => {
    if (!expiresAt.value) return false
    // 在过期前 1 分钟刷新
    return Date.now() > expiresAt.value - 60 * 1000
  })

  // Token 管理
  function getAccessToken(): string | null {
    return localStorage.getItem(ACCESS_TOKEN_KEY)
  }

  function getRefreshToken(): string | null {
    return localStorage.getItem(REFRESH_TOKEN_KEY)
  }

  function setTokens(
    accessToken: string,
    refreshToken: string,
    session: string,
    expiresIn: number
  ) {
    localStorage.setItem(ACCESS_TOKEN_KEY, accessToken)
    localStorage.setItem(REFRESH_TOKEN_KEY, refreshToken)
    localStorage.setItem(SESSION_ID_KEY, session)

    const expireTime = Date.now() + expiresIn * 1000
    localStorage.setItem(EXPIRES_AT_KEY, expireTime.toString())
    expiresAt.value = expireTime
    sessionId.value = session

    // 设置自动刷新
    setupAutoRefresh(expiresIn)
  }

  function updateAccessToken(accessToken: string, expiresIn: number) {
    localStorage.setItem(ACCESS_TOKEN_KEY, accessToken)
    const expireTime = Date.now() + expiresIn * 1000
    localStorage.setItem(EXPIRES_AT_KEY, expireTime.toString())
    expiresAt.value = expireTime

    setupAutoRefresh(expiresIn)
  }

  function clearTokens() {
    clearStoredAuthTokens()
    expiresAt.value = null
    sessionId.value = null

    if (refreshTimer) {
      clearTimeout(refreshTimer)
      refreshTimer = null
    }
  }

  // 自动刷新设置
  function setupAutoRefresh(expiresIn: number) {
    if (refreshTimer) {
      clearTimeout(refreshTimer)
    }

    // 在过期前 1 分钟刷新
    const refreshDelay = Math.max((expiresIn - 60) * 1000, 10000)

    refreshTimer = setTimeout(async () => {
      await refreshAccessToken()
    }, refreshDelay)
  }

  // 刷新 Access Token
  async function refreshAccessToken(): Promise<boolean> {
    const refreshToken = getRefreshToken()
    if (!refreshToken) {
      console.log('[Auth] 没有 Refresh Token，无法刷新')
      return false
    }

    try {
      console.log('[Auth] 调用刷新 API')
      const refreshed = await refreshStoredAccessToken()
      if (!refreshed) {
        throw new Error('Refresh Token 无效或已过期')
      }
      updateAccessToken(refreshed.accessToken, refreshed.expiresIn)
      console.log('[Auth] 刷新成功，新 Token 有效期', refreshed.expiresIn, '秒')
      return true
    } catch (error) {
      console.log('[Auth] 刷新 API 失败', error)
      // 刷新失败，清除登录状态
      clearTokens()
      isAuthenticated.value = false
      username.value = null
      return false
    }
  }

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
  }

  // 检查认证状态
  async function checkAuth(): Promise<boolean> {
    const token = getAccessToken()
    const refreshToken = getRefreshToken()
    const storedExpiresAt = localStorage.getItem(EXPIRES_AT_KEY)

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
      expiresAt.value = parseInt(storedExpiresAt, 10)
    }

    // 恢复 session ID
    sessionId.value = localStorage.getItem(SESSION_ID_KEY)

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
      const refreshed = await refreshAccessToken()
      if (refreshed) {
        return checkAuth()
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
