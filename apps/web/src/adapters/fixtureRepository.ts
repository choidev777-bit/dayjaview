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
import similarGated from '../../../../contracts/fixtures/similar/gated.json';
import unavailableError from '../../../../contracts/fixtures/errors/unavailable.json';
import type {
  AuthSession,
  EvidenceResponse,
  HistoricalAccessResponse,
  ProductRepository,
  RankingResponse,
  SavedItem,
  SavedResponse,
  SavedType,
  ThemeDetailResponse,
  TreemapResponse,
} from '../domain/contracts';
import { RepositoryError } from '../domain/repositoryErrors';

export type RankingFixture = 'live' | 'delayed' | 'degraded' | 'closed' | 'empty' | 'unavailable';
export type TreemapFixture = 'live' | 'excluded';
export type DetailFixture =
  | 'searching'
  | 'single'
  | 'multi'
  | 'none'
  | 'reemergence'
  | 'closed'
  | 'unmatched';
export type EvidenceFixture =
  | 'searching'
  | 'single'
  | 'multi'
  | 'none'
  | 'reemergence'
  | 'afterClose'
  | 'delayed'
  | 'degraded';
export type SavedFixture = 'library' | 'unavailable' | 'mixed';
export type FixtureResource = 'rankings' | 'treemap' | 'detail' | 'evidence' | 'saved' | 'historical';

export interface FixtureRepositoryOptions {
  authenticated?: boolean;
  latencyMs?: number;
  ranking?: RankingFixture;
  treemap?: TreemapFixture;
  detail?: DetailFixture;
  evidence?: EvidenceFixture;
  saved?: SavedFixture;
  failures?: FixtureResource[];
  permissions?: FixtureResource[];
}

const rankings: Record<RankingFixture, RankingResponse> = {
  live: rankingLive as unknown as RankingResponse,
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

const details: Record<Exclude<DetailFixture, 'none' | 'reemergence'>, ThemeDetailResponse> = {
  searching: detailSearching as unknown as ThemeDetailResponse,
  single: detailSingleSource as unknown as ThemeDetailResponse,
  multi: detailMultiSource as unknown as ThemeDetailResponse,
  closed: detailAfterClose as unknown as ThemeDetailResponse,
  unmatched: detailUnmatched as unknown as ThemeDetailResponse,
};

const evidence: Record<Exclude<EvidenceFixture, 'reemergence' | 'afterClose' | 'delayed'>, EvidenceResponse> = {
  searching: evidenceSearching as unknown as EvidenceResponse,
  single: evidenceSingleSource as unknown as EvidenceResponse,
  multi: evidenceMultiSource as unknown as EvidenceResponse,
  none: evidenceNoNew as unknown as EvidenceResponse,
  degraded: evidenceSourceDegraded as unknown as EvidenceResponse,
};

const libraryResponse = savedLibrary as unknown as SavedResponse;
const unavailableResponse = savedUnavailable as unknown as SavedResponse;

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

function selectedDetail(fixture: DetailFixture): ThemeDetailResponse {
  if (fixture === 'none') {
    const response = clone(details.searching);
    response.data.evidenceSummary.evidenceStatus = 'NO_NEW_CATALYST';
    return response;
  }
  if (fixture === 'reemergence') {
    const response = clone(details.single);
    response.data.evidenceSummary.evidenceStatus = 'REEMERGENCE';
    return response;
  }
  return details[fixture];
}

function defaultEvidenceFixture(detail: DetailFixture): EvidenceFixture {
  return {
    searching: 'searching',
    single: 'single',
    multi: 'multi',
    none: 'none',
    reemergence: 'reemergence',
    closed: 'afterClose',
    unmatched: 'single',
  }[detail] as EvidenceFixture;
}

function selectedEvidence(fixture: EvidenceFixture): EvidenceResponse {
  if (fixture === 'delayed') {
    const response = clone(evidence.single);
    response.meta.marketContext = clone(rankings.delayed.meta.marketContext);
    return response;
  }
  if (fixture === 'reemergence') {
    const response = clone(evidence.single);
    response.data.evidenceStatus = 'REEMERGENCE';
    return response;
  }
  if (fixture === 'afterClose') {
    const response = clone(evidence.multi);
    response.data.evidenceStatus = 'AFTER_CLOSE_CONFIRMED';
    response.meta = clone(details.closed.meta);
    return response;
  }
  return evidence[fixture];
}

export function createFixtureRepository(options: FixtureRepositoryOptions = {}): ProductRepository {
  let authenticated = options.authenticated ?? true;
  const failures = new Set(options.failures ?? []);
  const permissions = new Set(options.permissions ?? []);
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
    if (permissions.has(resource)) {
      throw new RepositoryError({
        kind: 'permission',
        status: 403,
        code: 'FEATURE_NOT_ENTITLED',
        message: '현재 계정으로는 이 데이터에 접근할 수 없습니다.',
      });
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
    getRankings: () => resolveFixture('rankings', rankings[options.ranking ?? 'live']),
    getTreemap: () => resolveFixture('treemap', treemaps[options.treemap ?? 'live']),
    getThemeDetail: () => resolveFixture('detail', selectedDetail(options.detail ?? 'single')),
    getEvidence: () =>
      resolveFixture(
        'evidence',
        selectedEvidence(options.evidence ?? defaultEvidenceFixture(options.detail ?? 'single')),
      ),
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
  } satisfies ProductRepository;
}
