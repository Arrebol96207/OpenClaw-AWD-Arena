import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const devHost = process.env.VITE_DEV_HOST || '127.0.0.1'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    host: devHost,
    proxy: {
      '/api': 'http://localhost:8000',
      '/health': 'http://localhost:8000',
      '/ws/': { target: 'ws://localhost:8000', ws: true },
    },
  },
  build: {
    // Split heavy 3rd-party deps into separate chunks for faster first paint.
    chunkSizeWarningLimit: 700,
    cssMinify: 'esbuild',
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes('node_modules/react') || id.includes('node_modules/react-dom') || id.includes('node_modules/react-router-dom')) {
            return 'react-vendor'
          }
          if (id.includes('node_modules/lucide-react')) {
            return 'icons'
          }
          if (id.includes('node_modules/vis-network')) {
            return 'visualization'
          }
          return undefined
        },
      },
    },
  },
})
