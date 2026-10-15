import path from 'node:path'
import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': path.resolve(import.meta.dirname, './src'),
    },
  },
  server: {
    proxy: {
      // Dev-only convenience: makes the Vite dev server and the backend
      // API look like the SAME origin to the browser (this task's own
      // cookie/origin design -- no CORS, no cross-site cookie issues).
      // In production, Caddy does the identical job (routes /api on the
      // dashboard hostname to the api service) -- a Phase 7 deployment
      // concern, not duplicated here.
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test-setup.ts'],
    globals: true,
    exclude: ['e2e/**', 'node_modules/**'],
  },
})
