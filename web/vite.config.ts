import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'
import { resolve } from 'path'

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  // 读取 .env.[mode]，未配置时用默认值（见 .env.development / .env.production）
  const env = loadEnv(mode, process.cwd(), 'VITE_')
  const proxyTarget = env.VITE_DEV_PROXY_TARGET || 'http://127.0.0.1:8000'
  const vuePlugin = vue()
  // This application is an SPA. Component acceptance tests mount a custom
  // Vue renderer, so compile the same client template used in production.
  if (mode === 'test' && vuePlugin.transform) {
    const hook = vuePlugin.transform
    const transform = typeof hook === 'function' ? hook : hook.handler
    vuePlugin.transform = function (code, id, options) {
      return transform.call(this, code, id, { ...options, ssr: false })
    }
  }

  return {
    plugins: [vuePlugin],
    resolve: {
      alias: {
        '@': resolve(__dirname, 'src'),
      },
    },
    server: {
      host: '127.0.0.1',
      port: 3000,
      proxy: {
        '/api': {
          target: proxyTarget,
          changeOrigin: true,
        },
        '/ws': {
          target: proxyTarget.replace(/^http/, 'ws'),
          ws: true,
        },
      },
    },
    build: {
      rollupOptions: {
        output: {
          // 分包：第三方框架与 UI 库独立，业务域按路由自然分包
          manualChunks: {
            'vendor-vue': ['vue', 'vue-router', 'pinia'],
            'vendor-naive': ['naive-ui'],
          },
        },
      },
    },
  }
})
