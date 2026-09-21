import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import router from './router'
import { useThemeStore } from './stores/theme'
import { useAuthStore } from './stores/auth'
import { initializeApiBaseUrl } from './api'
import './style.css'
import './styles/ios-theme.css'

const app = createApp(App)
const pinia = createPinia()

app.use(pinia)
app.use(router)

// 初始化主题
const themeStore = useThemeStore()
themeStore.initTheme()

async function bootstrap() {
  await initializeApiBaseUrl()
  const authStore = useAuthStore()
  await authStore.checkAuth()
  await router.isReady()
  app.mount('#app')
}

void bootstrap()
