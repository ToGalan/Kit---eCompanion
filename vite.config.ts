import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// The frontend and the backend are one app on two ports. Both halves read these, and
// `npm run dev` passes them to the API process, so the proxy target and the server it
// points at cannot drift apart.
const WEB_PORT = Number(process.env.KIT_WEB_PORT || 5175);
const API_PORT = Number(process.env.KIT_API_PORT || 8002);

export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    port: WEB_PORT,
    proxy: {
      '/api': {
        target: `http://127.0.0.1:${API_PORT}`,
        changeOrigin: true,
        // Without this a stopped backend surfaces as an opaque 500 from the proxy,
        // which reads as "the API is broken" rather than "the API is not running".
        configure: (proxy) => {
          proxy.on('error', (error, _request, response) => {
            const detail = `Kit's backend is not answering on port ${API_PORT} (${error.message}). Start both halves with: npm run dev`;
            console.error(`\n[proxy] ${detail}\n`);
            if (response && 'writeHead' in response && !response.headersSent) {
              response.writeHead(503, { 'Content-Type': 'application/json' });
              response.end(JSON.stringify({ ok: false, error: 'backend_unavailable', detail }));
            }
          });
        },
      },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: './vitest.setup.ts',
  },
});
