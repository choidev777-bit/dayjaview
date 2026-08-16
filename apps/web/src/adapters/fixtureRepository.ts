import rankingCalculationUnavailable from '../../../../contracts/fixtures/rankings/calculation-unavailable.json';
import rankingClosed from '../../../../contracts/fixtures/rankings/closed-pending-reconciliation.json';
import rankingDegraded from '../../../../contracts/fixtures/rankings/degraded-partial-coverage.json';
import rankingDelayed from '../../../../contracts/fixtures/rankings/delayed.json';
import rankingEmpty from '../../../../contracts/fixtures/rankings/empty.json';
import rankingLive from '../../../../contracts/fixtures/rankings/live.json';
import treemapExcluded from '../../../../contracts/fixtures/treemap/insufficient-coverage-excluded.json';
import treemapLive from '../../../../contracts/fixtures/treemap/live.json';
import detailAfterClose from '../../../../contracts/fixtures/event/after-close-confirmed.json';
import detailMultiSource from '../../../../contracts/fixtures/event/multi-source.json';
import detailSearching from '../../../../contracts/fixtures/event/searching-evidence.json';
import detailSingleSource from '../../../../contracts/fixtures/event/single-source.json';
import detailUnmatched from '../../../../contracts/fixtures/event/unmatched.json';
import evidenceMultiSource from '../../../../contracts/fixtures/evidence/multi-source.json';
import evidenceNoNew from '../../../../contracts/fixtures/evidence/no-new-catalyst.json';
import evidenceSearching from '../../../../contracts/fixtures/evidence/none-searching.json';
import evidenceSingleSource from '../../../../contracts/fixtures/evidence/single-source.json';
import evidenceSourceDegraded from '../../../../contracts/fixtures/evidence/source-degraded.json';
import savedLibrary from '../../../../contracts/fixtures/saved/library.json';
import savedUnavailable from '../../../../contracts/fixtures/saved/unavailable.json';
import similarAvailable from '../../../../contracts/fixtures/similar/available.json';
import similarEventDetail from '../../../../contracts/fixtures/similar/event-detail.json';
import similarGated from '../../../../contracts/fixtures/similar/gated.json';
import similarPartial from '../../../../contracts/fixtures/similar/partial-outcomes.json';
import unavailableError from '../../../../contracts/fixtures/errors/unavailable.json';
import {
  demoCatalystDetails,
  demoCatalystTop3,
  demoHistoricalEvents,
  demoRankings,
  demoSimilarEvents,
} from './demoStory';
import type {
  AuthSession,
  CatalystDetailResponse,
  CatalystTop3Response,
  EvidenceResponse,
  HistoricalAccessResponse,
  HistoricalEventResponse,
  ProductRepository,
  RankingResponse,
  SavedItem,
  SavedResponse,
  SavedType,
  SimilarEventsResponse,
  ThemeDetailResponse,
  TreemapResponse,
} from '../domain/contracts';
import { RepositoryError } from '../domain/repositoryErrors';

export type RankingFixture =
  | 'live'
  | 'demo'
  | 'delayed'
  | 'degraded'
  | 'closed'
  | 'empty'
  | 'unavailable';
export type TreemapFixture = 'live' | 'excluded';
export type DetailFixture = 'searching' | 'single' | 'multi' | 'closed' | 'unmatched';
export type EvidenceFixture = 'searching' | 'single' | 'multi' | 'none' | 'degraded';
export type SavedFixture = 'library' | 'unavailable' | 'mixed';
/** `demo`는 화면이 이어지는 원전수출 이야기, 나머지는 endpoint별 계약 fixture다. */
export type SimilarFixture = 'gated' | 'demo' | 'available' | 'partial';
export type FixtureResource = 'rankings' | 'treemap' | 'detail' | 'evidence' | 'saved' | 'historical';

export interface FixtureRepositoryOptions {
  authenticated?: boolean;
  latencyMs?: number;
  ranking?: RankingFixture;
  treemap?: TreemapFixture;
  detail?: DetailFixture;
  evidence?: EvidenceFixture;
  saved?: SavedFixture;
  similar?: SimilarFixture;
  failures?: FixtureResource[];
}

const rankings: Record<RankingFixture, RankingResponse> = {
  live: rankingLive as unknown as RankingResponse,
  demo: demoRankings,
  delayed: rankingDelayed as unknown as RankingResponse,
  degraded: rankingDegraded as unknown as RankingResponse,
  closed: rankingClosed as unknown as RankingResponse,
  empty: rankingEmpty as unknown as RankingResponse,
  unavailable: rankingCalculationUnavailable as unknown as RankingResponse,
};

const treemaps: Record<TreemapFixture, TreemapResponse> = {
  live: treemapLive as unknown as TreemapResponse,
  excluded: treemapExcluded as unknown as TreemapResponse,
};

const details: Record<DetailFixture, ThemeDetailResponse> = {
  searching: detailSearching as unknown as ThemeDetailResponse,
  single: detailSingleSource as unknown as ThemeDetailResponse,
  multi: detailMultiSource as unknown as ThemeDetailResponse,
  closed: detailAfterClose as unknown as ThemeDetailResponse,
  unmatched: detailUnmatched as unknown as ThemeDetailResponse,
};

const evidence: Record<EvidenceFixture, EvidenceResponse> = {
  searching: evidenceSearching as unknown as EvidenceResponse,
  single: evidenceSingleSource as unknown as EvidenceResponse,
  multi: evidenceMultiSource as unknown as EvidenceResponse,
  none: evidenceNoNew as unknown as EvidenceResponse,
  degraded: evidenceSourceDegraded as unknown as EvidenceResponse,
};

const libraryResponse = savedLibrary as unknown as SavedResponse;
const unavailableResponse = savedUnavailable as unknown as SavedResponse;

const similarEvents: Record<SimilarFixture, SimilarEventsResponse> = {
  gated: similarGated as unknown as SimilarEventsResponse,
  demo: demoSimilarEvents,
  available: similarAvailable as unknown as SimilarEventsResponse,
  partial: similarPartial as unknown as SimilarEventsResponse,
};

const historicalEvent = similarEventDetail as unknown as HistoricalEventResponse;

/**
 * 소재 유형 상세는 서버 계약이 없다(배선 매핑표 §5.1). 계약 fixture로 두면 없는 계약이 있는 것처럼
 * 보이므로 화면 검토용 표본만 여기 둔다. 수치는 screen_spec 8.7 형식(건수·중앙 반응)을 따른다.
 */
const catalystSample: CatalystDetailResponse = {
  data: {
    catalystId: 'ctl_sample',
    themeId: 'thm_nuclear',
    themeDisplayName: '원전수출',
    catalystName: '해외 원전 수주 단계 진전',
    availability: 'AVAILABLE',
    sameDay: {
      horizonTradingDays: 1,
      eligibleCount: 12,
      observedCount: 12,
      positiveCount: 8,
      medianReturn: 0.064,
    },
    horizons: [
      { horizonTradingDays: 1, eligibleCount: 12, observedCount: 12, positiveCount: 8, medianReturn: 0.021 },
      { horizonTradingDays: 5, eligibleCount: 12, observedCount: 11, positiveCount: 7, medianReturn: 0.038 },
      { horizonTradingDays: 20, eligibleCount: 12, observedCount: 8, positiveCount: 4, medianReturn: 0.012 },
    ],
    events: [
      {
        matchedEventId: 'evt_historical',
        marketDate: '2024-11-18',
        normalizedCatalystSummary: '마이크로 LED 양산 발표',
        sameDayReturn: 0.071,
        leaderName: '과거 예시 종목',
      },
      {
        matchedEventId: 'evt_partial_history',
        marketDate: '2025-08-20',
        normalizedCatalystSummary: '과거 확인 소재',
        sameDayReturn: null,
        leaderName: null,
      },
    ],
    qualityNote: '룰 기반 키워드라 검수 전 노이즈가 있을 수 있어요.',
  },
  meta: similarGated.meta as unknown as CatalystDetailResponse['meta'],
};

class ContractFixtureError extends RepositoryError {
  constructor() {
    super({
      kind: 'unavailable',
      message: unavailableError.error.message,
      code: unavailableError.error.code,
      retryable: unavailableError.error.retryable,
    });
  }
}

function clone<T>(value: T): T {
  return structuredClone(value);
}

export function createFixtureRepository(options: FixtureRepositoryOptions = {}): ProductRepository {
  let authenticated = options.authenticated ?? true;
  const failures = new Set(options.failures ?? []);
  const listeners = new Map<string, Set<() => void>>();
  const savedItems = new Map<string, SavedItem>();

  function savedKey(item: Pick<SavedItem, 'savedType' | 'targetId'>): string {
    return `${item.savedType}:${item.targetId}`;
  }

  function emit(resource: string) {
    listeners.get(resource)?.forEach((listener) => listener());
  }

  const mode = options.saved ?? 'mixed';
  const initialItems = [
    ...(mode === 'unavailable' ? [] : libraryResponse.data.items),
    ...(mode === 'library' ? [] : unavailableResponse.data.items),
  ];
  initialItems.forEach((item) => savedItems.set(savedKey(item), clone(item)));

  async function resolveFixture<T>(resource: FixtureResource, value: T): Promise<T> {
    if (options.latencyMs) {
      await new Promise((resolve) => window.setTimeout(resolve, options.latencyMs));
    } else {
      await Promise.resolve();
    }
    if (failures.has(resource)) throw new ContractFixtureError();
    return clone(value);
  }

  function selectedSavedItems(): SavedItem[] {
    return [...savedItems.values()].sort((left, right) => {
      const bySavedAt = right.savedAt.localeCompare(left.savedAt);
      return bySavedAt || left.targetId.localeCompare(right.targetId);
    });
  }

  return {
    subscribe(resource, listener) {
      const resourceListeners = listeners.get(resource) ?? new Set<() => void>();
      resourceListeners.add(listener);
      listeners.set(resource, resourceListeners);
      return () => resourceListeners.delete(listener);
    },
    getSession: async () => resolveFixture<AuthSession>('historical', { authenticated }),
    async startGoogleLogin() {
      authenticated = true;
      const session = await resolveFixture<AuthSession>('historical', { authenticated });
      emit('session');
      return session;
    },
    async logout() {
      authenticated = false;
      await Promise.resolve();
      emit('session');
    },
    // 시연 이야기를 켜면 오늘 목록도 10개짜리 시연본을 쓴다. `today`를 직접 준 경우는 그쪽이 우선.
    getRankings: () =>
      resolveFixture(
        'rankings',
        rankings[options.ranking ?? (options.similar === 'demo' ? 'demo' : 'live')],
      ),
    getTreemap: () => resolveFixture('treemap', treemaps[options.treemap ?? 'live']),
    getThemeDetail: (themeId, eventId) => {
      const selected = details[options.detail ?? 'single'];
      const gated = (options.similar ?? 'gated') === 'gated';
      // 계약 fixture는 테마가 하나뿐이라 어느 테마를 눌러도 같은 상세가 나온다. 실제 endpoint는
      // themeId·eventId로 조회하므로, 목록에 있는 테마면 그 테마의 값으로 바꿔 눌러본 대로 보이게 한다.
      const ranked = rankings[
        options.ranking ?? (options.similar === 'demo' ? 'demo' : 'live')
      ].data.items.find((item) => item.classification.themeId === themeId);
      return resolveFixture('detail', {
        ...selected,
        data: {
          ...selected.data,
          ...(ranked
            ? {
                eventId: ranked.eventId,
                classification: ranked.classification,
                currentReaction: {
                  ...selected.data.currentReaction,
                  weightedReturn: ranked.weightedReturn,
                  advancingCount: ranked.advancingCount,
                  validCount: ranked.validCount,
                },
                coverage: ranked.coverage,
              }
            : { eventId }),
          historicalAccess: gated
            ? selected.data.historicalAccess
            : { status: 'AVAILABLE' as const, reason: 'DEMO_STORY' },
        },
      });
    },
    getEvidence: () => resolveFixture('evidence', evidence[options.evidence ?? 'single']),
    async getSaved(type: SavedType | 'ALL') {
      const response: SavedResponse = {
        data: {
          items: selectedSavedItems().filter((item) => type === 'ALL' || item.savedType === type),
        },
        meta: libraryResponse.meta,
      };
      return resolveFixture('saved', response);
    },
    async saveSaved(item) {
      if (failures.has('saved')) throw new ContractFixtureError();
      const key = savedKey(item);
      if (!savedItems.has(key)) {
        savedItems.set(key, {
          savedType: item.savedType,
          targetId: item.targetId,
          displayName: item.displayName ?? item.targetId,
          savedAt: libraryResponse.meta.generatedAt,
          availability: 'AVAILABLE',
          unavailableReason: null,
          currentState: item.currentState ?? null,
        });
      }
      await Promise.resolve();
      emit('saved');
    },
    async removeSaved(item) {
      if (failures.has('saved')) throw new ContractFixtureError();
      savedItems.delete(savedKey(item));
      await Promise.resolve();
      emit('saved');
    },
    async getHistoricalAccess(eventId) {
      const response: HistoricalAccessResponse = {
        data: {
          eventId,
          availability: similarGated.data.availability === 'GATED' ? 'GATED' : 'UNAVAILABLE',
        },
        meta: similarGated.meta,
      };
      const resolved = await resolveFixture('historical', response);
      return resolved.data;
    },
    getSimilarEvents: (eventId) => {
      // summary는 기간별 분모를 나란히 보여주는 자리라 계약 응답 전체를 그대로 준다.
      // horizonTradingDays는 사례 행에 어느 기간 결과를 쓸지 고르는 값이고 summary를 자르지 않는다.
      const selected = similarEvents[options.similar ?? 'gated'];
      return resolveFixture('historical', {
        ...selected,
        data: { ...selected.data, eventId },
      });
    },
    // 시연 이야기에 있는 id면 그 사건을 준다. 없으면 계약 fixture로 떨어진다.
    getHistoricalEvent: (matchedEventId) =>
      resolveFixture(
        'historical',
        demoHistoricalEvents[matchedEventId] ?? {
          ...historicalEvent,
          data: { ...historicalEvent.data, eventId: matchedEventId },
        },
      ),
    getCatalystDetail: (catalystId) =>
      resolveFixture(
        'historical',
        demoCatalystDetails[catalystId] ?? {
          ...catalystSample,
          data: { ...catalystSample.data, catalystId },
        },
      ),
    getCachedRank: (eventId) =>
      rankings[options.ranking ?? (options.similar === 'demo' ? 'demo' : 'live')].data.items.find(
        (item) => item.eventId === eventId,
      )?.rank ?? null,
    getCatalystTop3: (themeId, eventId) =>
      resolveFixture<CatalystTop3Response>('historical', {
        ...demoCatalystTop3,
        data: { ...demoCatalystTop3.data, themeId, eventId },
      }),
  } satisfies ProductRepository;
}
