import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      output: {
        // Split rarely-changing vendor code into its own chunk so it stays in
        // the browser cache across app deploys — only changed app code
        // re-downloads, instead of the whole bundle every time.
        //
        // Function form, NOT the object form: with the object form each
        // listed package drags its not-yet-assigned deps into ITS chunk, and
        // react-query (listed under data-vendor) claimed react itself —
        // verified in the built output, where react.production.min.js ended
        // up inside data-vendor. Routing by module id assigns React core
        // first, unconditionally.
        manualChunks(id: string) {
          if (!id.includes('node_modules')) return
          if (/[\\/]node_modules[\\/](react|react-dom|scheduler)[\\/]/.test(id)) {
            return 'react-vendor'
          }
          if (/[\\/]node_modules[\\/](@tanstack|axios|zustand|use-sync-external-store)[\\/]/.test(id)) {
            return 'data-vendor'
          }
        },
      },
    },
  },
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
