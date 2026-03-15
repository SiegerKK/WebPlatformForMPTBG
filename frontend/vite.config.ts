import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    proxy: {
      '/api': {
        // In local dev (npm run dev), the API runs on localhost:8000.
        // Inside Docker the nginx conf handles the /api proxy to the backend container.
        target: 'http://localhost:8000',
        changeOrigin: true,
        ws: true,
      }
    }
  }
})
