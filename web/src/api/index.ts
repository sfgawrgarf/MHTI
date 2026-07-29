import axios, { AxiosError, type InternalAxiosRequestConfig } from 'axios'

const api = axios.create({
  baseURL: '/api',
  timeout: 60000, // 60秒，兼容长时间刮削操作
  headers: {
    'Content-Type': 'application/json',
  },
})

// Token 存储键（与 auth store 保持一致）
const ACCESS_TOKEN_KEY = 'access_token'
const REFRESH_TOKEN_KEY = 'refresh_token'
const SESSION_ID_KEY = 'session_id'
const EXPIRES_AT_KEY = 'expires_at'

// 所有并发请求共享同一次刷新，并且无论成功或失败都会被 settle。
let refreshPromise: Promise<string | null> | null = null

// 获取 token
function getToken(): string | null {
  return localStorage.getItem(ACCESS_TOKEN_KEY)
}

// 获取 refresh token
function getRefreshToken(): string | null {
  return localStorage.getItem(REFRESH_TOKEN_KEY)
}

// 获取过期时间
function getExpiresAt(): number | null {
  const expires = localStorage.getItem(EXPIRES_AT_KEY)
  return expires ? parseInt(expires, 10) : null
}

// 检查 token 是否即将过期（提前 2 分钟）
function isTokenExpiringSoon(): boolean {
  const expiresAt = getExpiresAt()
  if (!expiresAt) return false
  // 提前 2 分钟刷新
  return Date.now() > expiresAt - 2 * 60 * 1000
}

// 更新 token
function updateTokens(accessToken: string, expiresIn: number) {
  localStorage.setItem(ACCESS_TOKEN_KEY, accessToken)
  const expireTime = Date.now() + expiresIn * 1000
  localStorage.setItem(EXPIRES_AT_KEY, expireTime.toString())
}

// 清除 token
function clearTokens() {
  localStorage.removeItem(ACCESS_TOKEN_KEY)
  localStorage.removeItem(REFRESH_TOKEN_KEY)
  localStorage.removeItem(SESSION_ID_KEY)
  localStorage.removeItem(EXPIRES_AT_KEY)
}

// 刷新 token
async function refreshAccessToken(): Promise<string | null> {
  const refreshToken = getRefreshToken()
  if (!refreshToken) {
    console.log('[API] 没有 Refresh Token，无法刷新')
    return null
  }

  try {
    console.log('[API] 开始刷新 Token')
    // 使用原始 axios 避免拦截器循环
    const response = await axios.post('/api/auth/refresh', {
      refresh_token: refreshToken,
    })

    const { access_token, expires_in } = response.data
    updateTokens(access_token, expires_in)
    console.log('[API] Token 刷新成功，有效期', expires_in, '秒')
    return access_token
  } catch (error) {
    console.error('[API] Token 刷新失败', error)
    return null
  }
}

function getSharedRefresh(): Promise<string | null> {
  if (!refreshPromise) {
    refreshPromise = refreshAccessToken().finally(() => {
      refreshPromise = null
    })
  }
  return refreshPromise
}

// 请求拦截器
api.interceptors.request.use(
  async (config: InternalAxiosRequestConfig) => {
    // 跳过认证相关接口
    const authPaths = ['/auth/login', '/auth/register', '/auth/refresh', '/auth/status']
    if (authPaths.some((path) => config.url?.includes(path))) {
      const token = getToken()
      if (token && !config.url?.includes('/auth/refresh')) {
        config.headers.Authorization = `Bearer ${token}`
      }
      return config
    }

    const token = getToken()
    if (!token) {
      return config
    }

    // 检查 token 是否即将过期
    if (isTokenExpiringSoon()) {
      console.log('[API] Token 即将过期，尝试刷新')
      const newToken = await getSharedRefresh()
      // 刷新失败时使用旧 token 继续，让响应拦截器统一完成登出。
      config.headers.Authorization = `Bearer ${newToken || token}`
    } else {
      config.headers.Authorization = `Bearer ${token}`
    }

    return config
  },
  (error) => {
    return Promise.reject(error)
  }
)

// 响应拦截器
api.interceptors.response.use(
  (response) => {
    return response
  },
  async (error: AxiosError) => {
    const originalRequest = error.config as InternalAxiosRequestConfig & { _retry?: boolean }

    // 401 未授权处理
    if (error.response?.status === 401 && originalRequest && !originalRequest._retry) {
      // 如果是 refresh 接口失败，清除 token 并跳转登录
      if (originalRequest.url?.includes('/auth/refresh')) {
        console.log('[API] Refresh Token 失效，跳转登录')
        clearTokens()
        if (window.location.pathname !== '/login') {
          window.location.href = '/login'
        }
        return Promise.reject(error)
      }

      originalRequest._retry = true

      try {
        const newToken = await getSharedRefresh()
        if (newToken) {
          originalRequest.headers.Authorization = `Bearer ${newToken}`
          return api(originalRequest)
        } else {
          // 刷新失败，跳转登录
          console.log('[API] 无法刷新 Token，跳转登录')
          clearTokens()
          if (window.location.pathname !== '/login') {
            window.location.href = '/login'
          }
          return Promise.reject(error)
        }
      } catch (refreshError) {
        console.error('[API] 刷新异常', refreshError)
        clearTokens()
        if (window.location.pathname !== '/login') {
          window.location.href = '/login'
        }
        return Promise.reject(error)
      }
    }

    // 其他错误处理
    const message = (error.response?.data as { detail?: string })?.detail || error.message || '请求失败'
    console.error('[API] 请求错误:', message)
    return Promise.reject(error)
  }
)

export default api
