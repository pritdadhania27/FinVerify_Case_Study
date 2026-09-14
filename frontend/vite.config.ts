import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The API runs separately (uvicorn on 8000). Proxying /api in dev means the
// frontend never needs a base URL baked in, and never needs CORS configured on
// a backend whose only deployment is local.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
})
