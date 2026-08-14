import type {
  AuthSession,
  EvidenceResponse,
  HistoricalAccessResponse,
  ProductRepository,
  RankingResponse,
  SavedResponse,
  SavedType,
  ThemeDetailResponse,
  TreemapResponse,
} from '../domain/contracts';
import { safeReturnTo } from '../domain/formatting';

class LiveAdapterPendingError extends Error {
  constructor() {
    super('실제 데이터 연결은 준비 중입니다. 잠시 후 다시 시도해 주세요.');
  }
}

async function pending<T>(): Promise<T> {
  throw new LiveAdapterPendingError();
}

export function createProductionRepository(): ProductRepository {
  return {
    async getSession() {
      try {
        const response = await fetch('/api/auth/session', {
          credentials: 'include',
          headers: { Accept: 'application/json' },
        });
        return { authenticated: response.ok };
      } catch {
        return { authenticated: false };
      }
    },
    async startGoogleLogin(returnTo) {
      const target = safeReturnTo(returnTo);
      window.location.assign(`/api/auth/google?returnTo=${encodeURIComponent(target)}`);
      return new Promise<AuthSession>(() => undefined);
    },
    async logout() {
      await fetch('/api/auth/logout', {
        method: 'POST',
        credentials: 'include',
        headers: { Accept: 'application/json' },
      });
    },
    getRankings: () => pending<RankingResponse>(),
    getTreemap: () => pending<TreemapResponse>(),
    getThemeDetail: () => pending<ThemeDetailResponse>(),
    getEvidence: () => pending<EvidenceResponse>(),
    getSaved: () => pending<SavedResponse>(),
    removeSaved: () => pending<void>(),
    getHistoricalAccess: () => pending<HistoricalAccessResponse>(),
  } satisfies ProductRepository;
}

export type { SavedType };
