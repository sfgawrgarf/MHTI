import { defineStore } from 'pinia'
import { ref } from 'vue'
import { configApi } from '@/api/config'
import type {
  ApiTokenStatus,
  ProxyConfigResponse,
  LanguageConfigResponse,
  NamingTemplate,
} from '@/api/types'

export const useSettingsStore = defineStore('settings', () => {
  // API Token 状态
  const apiTokenStatus = ref<ApiTokenStatus | null>(null)
  const apiTokenLoading = ref(false)

  // 代理配置
  const proxyConfig = ref<ProxyConfigResponse | null>(null)
  const proxyLoading = ref(false)

  // 语言配置
  const languageConfig = ref<LanguageConfigResponse | null>(null)
  const languageLoading = ref(false)

  // 模板配置
  const templateConfig = ref<NamingTemplate | null>(null)
  const templateLoading = ref(false)

  // API Token Actions
  async function fetchApiTokenStatus() {
    apiTokenLoading.value = true
    try {
      apiTokenStatus.value = await configApi.getApiTokenStatus()
    } finally {
      apiTokenLoading.value = false
    }
  }

  async function saveApiToken(token: string) {
    apiTokenLoading.value = true
    try {
      const response = await configApi.saveApiToken(token)
      apiTokenStatus.value = response.status
      return response
    } finally {
      apiTokenLoading.value = false
    }
  }

  // Proxy Actions
  async function fetchProxyConfig() {
    proxyLoading.value = true
    try {
      proxyConfig.value = await configApi.getProxyConfig()
    } finally {
      proxyLoading.value = false
    }
  }

  // Language Actions
  async function fetchLanguageConfig() {
    languageLoading.value = true
    try {
      languageConfig.value = await configApi.getLanguageConfig()
    } finally {
      languageLoading.value = false
    }
  }

  // Template Actions
  async function fetchTemplateConfig() {
    templateLoading.value = true
    try {
      templateConfig.value = await configApi.getNamingConfig()
    } finally {
      templateLoading.value = false
    }
  }

  // 初始化所有配置
  async function initAllSettings() {
    await Promise.all([
      fetchApiTokenStatus(),
      fetchProxyConfig(),
      fetchLanguageConfig(),
      fetchTemplateConfig(),
    ])
  }

  return {
    // State
    apiTokenStatus,
    apiTokenLoading,
    proxyConfig,
    proxyLoading,
    languageConfig,
    languageLoading,
    templateConfig,
    templateLoading,
    // Actions
    fetchApiTokenStatus,
    saveApiToken,
    fetchProxyConfig,
    fetchLanguageConfig,
    fetchTemplateConfig,
    initAllSettings,
  }
})
