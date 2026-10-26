import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    // 显式绑 IPv4 回环：默认 host=localhost 在 Windows 上只监听 [::1]，
    // 浏览器访问 http://127.0.0.1:5173 会连不上（README 里写的就是这个地址）。
    host: '127.0.0.1',
    // 开发环境双端口：Vite 把 /api 代理到 FastAPI
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true
      }
    }
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    rollupOptions: {
      // 两个入口：订单页（生产）+ 边界用例测试页（/tests.html，接口关闭时页面提示如何开启）
      input: {
        index: 'index.html',
        tests: 'tests.html'
      }
    }
  }
})
