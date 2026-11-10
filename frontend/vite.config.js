import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// In development the API runs on :8000. Proxying /api keeps the browser on one
// origin, so no CORS is involved. In production FastAPI serves this build.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': 'http://localhost:8000',
    },
  },
})
