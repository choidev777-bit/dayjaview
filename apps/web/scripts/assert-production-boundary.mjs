import { readdir, readFile } from 'node:fs/promises';
import { resolve } from 'node:path';

const distRoot = resolve(process.cwd(), 'dist');
const forbiddenMarkers = [
  'contracts/fixtures',
  'snap_rank_live',
  'req_event_single',
  'evt_historical',
  'FixtureRepository',
];

async function collectFiles(directory) {
  const entries = await readdir(directory, { withFileTypes: true });
  const nested = await Promise.all(
    entries.map((entry) => {
      const path = resolve(directory, entry.name);
      return entry.isDirectory() ? collectFiles(path) : [path];
    }),
  );
  return nested.flat();
}

const files = await collectFiles(distRoot);

if (files.some((file) => file.endsWith('fixture.html'))) {
  throw new Error('production build에 fixture entry가 포함되었습니다.');
}

for (const file of files) {
  const contents = await readFile(file, 'utf8');
  const marker = forbiddenMarkers.find((candidate) => contents.includes(candidate));
  if (marker) {
    throw new Error(`production build에 계약 fixture marker가 포함되었습니다: ${marker}`);
  }
}

console.log(`production fixture boundary 확인: ${files.length}개 산출물`);
