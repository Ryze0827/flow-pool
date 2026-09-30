import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const target = env.FLOWPOOL_DEV_API_TARGET || 'http://127.0.0.1:8765'
  return {
    plugins: [vue()],
    server: { host: '127.0.0.1', port: 5173, proxy: { '/api': {
      target,
      changeOrigin: true,
      configure(proxy) {
        proxy.on('proxyReq', (request, incoming) => {
          // 同源本地页面经代理访问后台时，Origin 与转发后的 Host 保持一致。
          if (incoming.headers.origin === `http://${incoming.headers.host}`) {
            request.setHeader('Origin', new URL(target).origin)
          }
        })
      }
    } } }
  }
})
