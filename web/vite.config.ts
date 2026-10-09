import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// 构建产物直接写进 Python 包，`uv tool install` 不需要 Node 就能带上前端。
// 开发时 `npm run dev`，/api 代理到本机 `log-agent serve`（默认 8765 端口）。
export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: {
    outDir: '../src/log_agent/web/static',
    emptyOutDir: true,
  },
  server: {
    proxy: {
      '/api': { target: 'http://127.0.0.1:8765', changeOrigin: false },
    },
  },
})
