import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

describe('production fixture 경계', () => {
  it('production entry는 fixture adapter를 import하지 않는다', async () => {
    const entry = await readFile(resolve(process.cwd(), 'src/main.tsx'), 'utf8');
    expect(entry).not.toContain('fixtureRepository');
    expect(entry).toContain('productionRepository');
  });

  it('fixture entry는 별도 HTML에만 연결된다', async () => {
    const productionHtml = await readFile(resolve(process.cwd(), 'index.html'), 'utf8');
    const fixtureHtml = await readFile(resolve(process.cwd(), 'fixture.html'), 'utf8');
    expect(productionHtml).toContain('/src/main.tsx');
    expect(productionHtml).not.toContain('main.fixture');
    expect(fixtureHtml).toContain('/src/main.fixture.tsx');
  });

  it('운영자 콘솔은 사용자 entry와 분리된 별도 HTML로만 연결된다', async () => {
    const productionHtml = await readFile(resolve(process.cwd(), 'index.html'), 'utf8');
    const operatorHtml = await readFile(resolve(process.cwd(), 'operator.html'), 'utf8');
    const operatorEntry = await readFile(resolve(process.cwd(), 'src/main.operator.tsx'), 'utf8');
    const userShell = await readFile(resolve(process.cwd(), 'src/app/App.tsx'), 'utf8');
    expect(productionHtml).not.toContain('main.operator');
    expect(operatorHtml).toContain('/src/main.operator.tsx');
    expect(operatorHtml).toContain('noindex');
    expect(operatorEntry).not.toContain('fixtureRepository');
    expect(userShell).not.toContain('operator');
  });
});
