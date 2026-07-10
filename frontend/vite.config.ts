import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        // Use 127.0.0.1, NOT localhost: on Windows/Node 18+, `localhost`
        // resolves to IPv6 ::1 first, but uvicorn binds to IPv4 127.0.0.1 —
        // so a `localhost` target hits ::1, gets refused, and each request
        // eats a ~2s connection timeout before failing (500s + a spinning UI).
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
})
