import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (!id.includes('node_modules')) return undefined
          if (
            id.includes('/react/') ||
            id.includes('/react-dom/') ||
            id.includes('/react-is/') ||
            id.includes('/react-router-dom/')
          ) {
            return 'react-vendor'
          }
          if (id.includes('/recharts/') || id.includes('/d3-')) {
            return 'charts'
          }
          if (id.includes('/axios/')) {
            return 'http-vendor'
          }
          return undefined
        },
      },
    },
  },
})
