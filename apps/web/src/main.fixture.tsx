import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { App } from './app/App';
import {
  createFixtureRepository,
  type DetailFixture,
  type EvidenceFixture,
  type FixtureResource,
  type RankingFixture,
  type SavedFixture,
  type TreemapFixture,
} from './adapters/fixtureRepository';
import './styles/tokens.css';
import './styles/global.css';

const params = new URLSearchParams(window.location.search);

function oneOf<T extends string>(value: string | null, values: readonly T[], fallback: T): T {
  return value && values.includes(value as T) ? (value as T) : fallback;
}

const failure = params.get('error');
const repository = createFixtureRepository({
  authenticated: params.get('auth') !== 'anonymous',
  latencyMs: params.get('slow') === 'true' ? 900 : 0,
  ranking: oneOf<RankingFixture>(params.get('today'), ['live', 'delayed', 'degraded', 'closed', 'empty', 'unavailable'], 'live'),
  treemap: oneOf<TreemapFixture>(params.get('insights'), ['live', 'excluded'], 'live'),
  detail: oneOf<DetailFixture>(params.get('detail'), ['searching', 'single', 'multi', 'closed', 'unmatched'], 'single'),
  evidence: oneOf<EvidenceFixture>(params.get('evidence'), ['searching', 'single', 'multi', 'none', 'degraded'], 'single'),
  saved: oneOf<SavedFixture>(params.get('saved'), ['library', 'unavailable', 'mixed'], 'mixed'),
  failures: failure
    ? [oneOf<FixtureResource>(failure, ['rankings', 'treemap', 'detail', 'evidence', 'saved', 'historical'], 'rankings')]
    : [],
});

const root = document.getElementById('root');
if (!root) throw new Error('애플리케이션 root를 찾을 수 없습니다.');

createRoot(root).render(
  <StrictMode>
    <App repository={repository} />
  </StrictMode>,
);
