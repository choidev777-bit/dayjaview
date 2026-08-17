/**
 * prototype 레포에 올릴 fixture 미리보기 전용 빌드.
 *
 * 실서비스 번들(index.html)에는 시연 데이터가 들어가면 안 되지만, 이 산출물은
 * 시연 데이터를 보여주는 게 목적이라 fixture 진입점을 index.html로 굽는다.
 * 실서비스 배포에는 쓰지 않는다.
 */
import { fileURLToPath } from 'node:url';
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

export default defineConfig({
  plugins: [react()],
  base: './',
  build: {
    outDir: 'dist-preview',
    emptyOutDir: true,
    rollupOptions: {
      input: fileURLToPath(new URL('./fixture.html', import.meta.url)),
    },
  },
});
