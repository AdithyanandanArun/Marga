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
        // The local gateway binds to IPv4. Pinning this avoids a browser/dev
        // session depending on how `localhost` resolves on the host.
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        ws: true,
      },
      // The mobility graph router (services/mobility_graph/api.py) is
      // mounted in the gateway without a /v1 prefix — proxy it separately
      // rather than renaming it out of step with the backend's own routes.
      '/graph': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        ws: true,
      },
    },
  },
});
