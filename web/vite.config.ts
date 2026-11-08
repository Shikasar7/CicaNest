import path from 'node:path'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Dev server proxies the API to a running `nest serve` (default port 18800).
const backend = process.env.NEST_BACKEND ?? 'http://127.0.0.1:18800'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { '@': path.resolve(__dirname, './src') },
  },
  server: {
    proxy: {
      // nest refuses writes whose Origin is not its own address: present the backend's.
      '/api': { target: backend, changeOrigin: true, headers: { origin: backend } },
      '/login': { target: backend, changeOrigin: true, headers: { origin: backend } },
    },
  },
})
