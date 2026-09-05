import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'path';

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    port: 3000,
    proxy: {
      '/v1': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        ws: true,
      },
      // The mobility graph router (services/mobility_graph/api.py) is
      // mounted in the gateway without a /v1 prefix — proxy it separately
      // rather than renaming it out of step with the backend's own routes.
      '/graph': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        ws: true,
      },
    },
  },
});
