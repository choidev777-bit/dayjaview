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
  type SimilarFixture,
  type TreemapFixture,
} from './adapters/fixtureRepository';
import './styles/tokens.css';
import './styles/global.css';
import { SafariPhoneFrame } from './shared/SafariPhoneFrame';

const params = new URLSearchParams(window.location.search);

function oneOf<T extends string>(value: string | null, values: readonly T[], fallback: T): T {
  return value && values.includes(value as T) ? (value as T) : fallback;
}

const failure = params.get('error');
const repository = createFixtureRepository({
  authenticated: params.get('auth') !== 'anonymous',
  latencyMs: params.get('slow') === 'true' ? 900 : 0,
  ranking: params.get('today')
    ? oneOf<RankingFixture>(
        params.get('today'),
        ['live', 'demo', 'delayed', 'degraded', 'closed', 'empty', 'unavailable'],
        'live',
      )
    : undefined,
  // 시연본은 계약 fixture 대신 구 DB 실데이터를 쓴다. 그래서 파라미터를 안 준 화면은
  // 값을 비워 두고, 어댑터가 시연 데이터를 고르게 한다. `?insights=live`처럼 명시하면 fixture로 돌아간다.
  treemap: params.get('insights')
    ? oneOf<TreemapFixture>(params.get('insights'), ['live', 'excluded'], 'live')
    : undefined,
  detail: oneOf<DetailFixture>(params.get('detail'), ['searching', 'single', 'multi', 'closed', 'unmatched'], 'single'),
  evidence: params.get('evidence')
    ? oneOf<EvidenceFixture>(params.get('evidence'), ['searching', 'single', 'multi', 'none', 'degraded'], 'single')
    : undefined,
  saved: params.get('saved')
    ? oneOf<SavedFixture>(params.get('saved'), ['library', 'unavailable', 'mixed'], 'mixed')
    : undefined,
  // 로컬에서는 화면이 이어지는 시연본을 기본으로 연다. 게이트가 닫힌 상태를 보려면 `?similar=gated`.
  similar: oneOf<SimilarFixture>(
    params.get('similar'),
    ['gated', 'demo', 'available', 'partial'],
    'demo',
  ),
  // 8/14 실제 체결로 만든 분 단위 스냅샷을 기본으로 흘린다. 정지본을 보려면 `?replay=off`.
  replay: params.get('replay') !== 'off',
  failures: failure
    ? [oneOf<FixtureResource>(failure, ['rankings', 'treemap', 'detail', 'evidence', 'saved', 'historical'], 'rankings')]
    : [],
});

const root = document.getElementById('root');
if (!root) throw new Error('애플리케이션 root를 찾을 수 없습니다.');

// 실기기에서는 브라우저가 이미 주소창과 하단 메뉴를 갖고 있다. 목업 프레임은 데스크톱에서
// 그 높이를 재보려고 만든 것이라 값을 명시할 때만 켠다.
//   phone  주소창·하단 메뉴가 있는 Safari. 실제로 깎이는 높이(672px)를 확인할 때
//   device 주소창·하단 메뉴가 없는 기기만. 전체화면(PWA)과 같고, 시연 영상 촬영용
const frame = params.get('frame');
// 목업 iframe 안쪽이면 스크롤 막대를 숨긴다. 실기기에는 없는 것이라 영상에 남으면 티가 난다.
if (frame === 'inner') document.documentElement.dataset.chromeless = 'true';
createRoot(root).render(
  <StrictMode>
    {frame === 'phone' || frame === 'device' ? (
      <SafariPhoneFrame bare={frame === 'device'} />
    ) : (
      <App repository={repository} />
    )}
  </StrictMode>,
);
