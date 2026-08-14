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

export type RankingFixture = 'live' | 'delayed' | 'degraded' | 'closed' | 'empty' | 'unavailable';
export type TreemapFixture = 'live' | 'excluded';
export type DetailFixture = 'searching' | 'single' | 'multi' | 'closed' | 'unmatched';
export type EvidenceFixture = 'searching' | 'single' | 'multi' | 'none' | 'degraded';
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

class ContractFixtureError extends Error {
  readonly code = unavailableError.error.code;

  constructor() {
    super(unavailableError.error.message);
  }
}

function clone<T>(value: T): T {
  return structuredClone(value);
}

export function createFixtureRepository(options: FixtureRepositoryOptions = {}): ProductRepository {
  let authenticated = options.authenticated ?? true;
  const removed = new Set<string>();
  const failures = new Set(options.failures ?? []);

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
    const mode = options.saved ?? 'mixed';
    const availableItems = mode === 'unavailable' ? [] : libraryResponse.data.items;
    const unavailableItems = mode === 'library' ? [] : unavailableResponse.data.items;
    return [...availableItems, ...unavailableItems].filter((item) => !removed.has(item.targetId));
  }

  return {
    getSession: async () => resolveFixture<AuthSession>('historical', { authenticated }),
    async startGoogleLogin() {
      authenticated = true;
      return resolveFixture<AuthSession>('historical', { authenticated });
    },
    async logout() {
      authenticated = false;
      await Promise.resolve();
    },
    getRankings: () => resolveFixture('rankings', rankings[options.ranking ?? 'live']),
    getTreemap: () => resolveFixture('treemap', treemaps[options.treemap ?? 'live']),
    getThemeDetail: () => resolveFixture('detail', details[options.detail ?? 'single']),
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
    async removeSaved(item) {
      if (failures.has('saved')) throw new ContractFixtureError();
      removed.add(item.targetId);
      await Promise.resolve();
    },
    async getHistoricalAccess(eventId) {
      const response: HistoricalAccessResponse = {
        data: {
          eventId,
          availability: similarGated.data.availability === 'GATED' ? 'GATED' : 'UNAVAILABLE',
        },
        meta: similarGated.meta,
      };
      return resolveFixture('historical', response);
    },
  } satisfies ProductRepository;
}
