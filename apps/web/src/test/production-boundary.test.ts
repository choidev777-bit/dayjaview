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

  it('production 산출물 검사에 evidence fixture marker가 포함된다', async () => {
    const boundaryScript = await readFile(
      resolve(process.cwd(), 'scripts/assert-production-boundary.mjs'),
      'utf8',
    );
    expect(boundaryScript).toContain('req_evidence_single');
    expect(boundaryScript).toContain('req_evidence_degraded');
  });
});
