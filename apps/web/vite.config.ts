import { fileURLToPath, URL } from 'node:url';
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

const repositoryRoot = fileURLToPath(new URL('../..', import.meta.url));

export default defineConfig(({ command, mode }) => ({
  plugins: [react()],
  // 개발 서버에서만(테스트 제외): WSS를 같은 origin의 /api 프록시로 보낸다.
  define:
    command === 'serve' && mode !== 'test'
      ? {
          'import.meta.env.VITE_REALTIME_URL': JSON.stringify(
            'ws://localhost:5173/api/v1/realtime',
          ),
        }
      : undefined,
  server: {
    fs: {
      allow: [repositoryRoot],
    },
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        ws: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
  build: {
    rollupOptions: {
      // 운영자 콘솔은 일반 사용자 SPA와 다른 entry다. 두 번들은 서로를 import하지 않고
      // 사용자 navigation·sitemap에도 나타나지 않는다. 접근 통제는 서버 role gate가 한다.
      input: [
        fileURLToPath(new URL('./index.html', import.meta.url)),
        fileURLToPath(new URL('./operator.html', import.meta.url)),
      ],
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    css: true,
  },
}));
