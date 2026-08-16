import { fileURLToPath, URL } from 'node:url';
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

const repositoryRoot = fileURLToPath(new URL('../..', import.meta.url));

// 개발 서버 기본값은 fixture shell이다. Vite 기본 SPA fallback은 모든 경로에 index.html을
// 주기 때문에, /today에서 새로고침하면 production entry가 뜨면서 로그인 화면으로 되돌아간다.
// 화면을 고치는 동안 매번 로그인하지 않도록 navigation 요청만 fixture.html로 돌린다.
// 실제 로그인·API 흐름을 확인할 때는 `--mode api`로 띄우면 이 rewrite가 빠진다.
function fixtureDevShell() {
  return {
    name: 'dayjaview-fixture-dev-shell',
    apply: 'serve' as const,
    configureServer(server: { middlewares: { use: (fn: DevMiddleware) => void } }) {
      server.middlewares.use((request, _response, next) => {
        const url = request.url ?? '/';
        const isNavigation =
          request.method === 'GET' && (request.headers.accept ?? '').includes('text/html');
        const isOwnEntry =
          url.startsWith('/api') || url.startsWith('/operator') || url.startsWith('/fixture.html');
        if (isNavigation && !isOwnEntry) request.url = '/fixture.html';
        next();
      });
    },
  };
}

type DevMiddleware = (
  request: { url?: string; method?: string; headers: { accept?: string } },
  response: unknown,
  next: () => void,
) => void;

export default defineConfig(({ command, mode }) => ({
  plugins: [react(), ...(mode === 'api' || mode === 'test' ? [] : [fixtureDevShell()])],
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
