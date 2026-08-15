export type DataStatus = 'PREOPEN' | 'LIVE' | 'DELAYED' | 'DEGRADED' | 'CLOSED';
export type LifecycleStatus = 'CANDIDATE' | 'ACTIVE' | 'WEAKENING' | 'CLOSED' | 'DISCARDED';
export type ReconciliationStatus = 'PENDING' | 'MATCHED' | 'UNMATCHED';
export type EvidenceStatus =
  | 'SEARCHING'
  | 'SINGLE_SOURCE'
  | 'MULTI_SOURCE_CONFIRMED'
  | 'NO_NEW_CATALYST'
  | 'REEMERGENCE'
  | 'AFTER_CLOSE_CONFIRMED';
export type CoverageStatus = 'SUFFICIENT' | 'PARTIAL' | 'INSUFFICIENT';

export interface MarketContext {
  market: string;
  timeZone: string;
  marketDate: string;
  asOf: string;
  dataStatus: DataStatus;
  lastHealthyAt: string | null;
  qualityFlags: string[];
}

export interface ResponseMeta {
  requestId: string;
  apiVersion: string;
  schemaVersion: string;
  generatedAt: string;
  marketContext?: MarketContext;
}

export interface CoverageLeg {
  observedCount: number;
  totalCount: number;
  countRatio: number | null;
  observedWeightRatio?: number | null;
}

export interface Coverage {
  status: CoverageStatus;
  core: CoverageLeg;
  related: CoverageLeg;
}

export interface Classification {
  classificationVersion: number;
  themeId: string;
  displayName: string;
  kind: 'INFOSTOCK_THEME' | 'UNCLASSIFIED_CLUSTER' | 'TEMPORARY_THEME';
  certainty: 'PROVISIONAL' | 'CONFIRMED';
  source: 'LIVE_ENGINE' | 'INFOSTOCK';
  changedAt: string;
}

export interface RankingItem {
  eventId: string;
  lifecycleStatus: LifecycleStatus;
  reconciliationStatus: ReconciliationStatus;
  classification: Classification;
  rank: number;
  rankChange60s: number | null;
  badges: string[];
  weightedReturn: number | null;
  weightMethod: 'FREE_FLOAT_CAPPED';
  advancingCount: number | null;
  validCount: number | null;
  leader: {
    stockId: string;
    symbol: string;
    name: string;
    return: number;
  } | null;
  evidence: {
    evidenceStatus: EvidenceStatus;
    summary: string | null;
    publishedAt: string | null;
  };
  coverage: Coverage;
  qualityFlags: string[];
}

export interface RankingResponse {
  data: {
    snapshotId: string;
    streamId: string;
    sequence: number;
    items: RankingItem[];
  };
  meta: ResponseMeta & { marketContext: MarketContext };
}

export interface TreemapItem {
  eventId: string;
  themeId: string;
  displayName: string;
  lifecycleStatus: Extract<LifecycleStatus, 'ACTIVE' | 'WEAKENING'>;
  weightedReturn: number;
  advancingCount: number;
  validCount: number;
  coverageStatus: CoverageStatus;
  qualityFlags: string[];
}

export interface TreemapResponse {
  data: {
    snapshotId: string;
    streamId: string;
    sequence: number;
    items: TreemapItem[];
  };
  meta: ResponseMeta & { marketContext: MarketContext };
}

export interface ThemeDetailResponse {
  data: {
    eventId: string;
    marketDate: string;
    lifecycleStatus: LifecycleStatus;
    reconciliationStatus: ReconciliationStatus;
    classification: Classification;
    currentReaction: {
      weightedReturn: number | null;
      weightMethod: 'FREE_FLOAT_CAPPED';
      advancingCount: number | null;
      validCount: number | null;
      turnoverMultiple: number | null;
      attentionGapTradingDays: number | null;
    };
    coverage: Coverage;
    evidenceSummary: {
      evidenceStatus: EvidenceStatus;
      summary: string | null;
      sourceCount: number;
      latestPublishedAt: string | null;
    };
    leaders: Array<{
      stockId: string;
      symbol: string;
      name: string;
      return: number;
      role: 'LEADER';
    }>;
    historicalAccess: {
      status: 'AVAILABLE' | 'GATED' | 'UNAVAILABLE';
      reason: string;
    };
    canonicalPath: string;
    qualityFlags: string[];
  };
  meta: ResponseMeta;
}

export type MatchBasis = 'THEME' | 'STOCK' | 'TIME';

export interface EvidenceItem {
  newsId: string;
  sourceName: string;
  title: string;
  publishedAt: string | null;
  receivedAt: string;
  originalUrl: string;
  matchBasis: MatchBasis[];
  summary: string;
  qualityFlags: string[];
}

export interface EvidenceResponse {
  data: {
    eventId: string;
    evidenceStatus: EvidenceStatus;
    items: EvidenceItem[];
    page: {
      nextCursor: string | null;
      hasMore: boolean;
      limit: number;
    };
  };
  meta: ResponseMeta;
}

export type SavedType = 'THEME' | 'STOCK' | 'EVENT';

export interface SavedItem {
  savedType: SavedType;
  targetId: string;
  displayName: string;
  savedAt: string;
  availability: 'AVAILABLE' | 'UNAVAILABLE';
  unavailableReason: string | null;
  currentState: {
    eventId: string;
    eventState: LifecycleStatus;
    weightedReturn: number;
    dataStatus: DataStatus;
    asOf: string;
  } | null;
}

export interface SavedResponse {
  data: { items: SavedItem[] };
  meta: ResponseMeta;
}

export interface SavedMutationResponse {
  data: {
    savedType: SavedType;
    targetId: string;
    saved: boolean;
    savedAt: string | null;
  };
  meta: ResponseMeta;
}

export interface SavedTarget {
  savedType: SavedType;
  targetId: string;
  displayName?: string;
  currentState?: SavedItem['currentState'];
}

export interface HistoricalAccessResponse {
  data: {
    eventId: string;
    availability: 'GATED' | 'UNAVAILABLE';
  };
  meta: ResponseMeta;
}

export interface AuthSession {
  authenticated: boolean;
}

export interface SessionResponse {
  data: {
    authenticated: boolean;
    user: { displayName: string } | null;
    roles: Array<'USER' | 'HISTORICAL_PILOT' | 'OPERATOR'>;
  };
  meta: ResponseMeta;
}

export interface RealtimeTicketResponse {
  data: {
    ticket: string;
    expiresAt: string;
  };
  meta: ResponseMeta;
}

export type RealtimeTopic =
  | 'theme_rank_snapshot'
  | 'theme_treemap_snapshot'
  | 'event_state_changed';

interface RealtimeSnapshotBase {
  type: RealtimeTopic;
  schemaVersion: string;
  subscriptionId: string;
  streamId: string;
  topic: RealtimeTopic;
  sequence: number;
  generatedAt: string;
  asOf: string;
  marketDate: string;
  dataStatus: DataStatus;
  qualityFlags: string[];
}

export interface RealtimeRankingSnapshot extends RealtimeSnapshotBase {
  type: 'theme_rank_snapshot';
  topic: 'theme_rank_snapshot';
  payload: {
    snapshotId: string;
    items: RankingItem[];
  };
}

export interface RealtimeTreemapSnapshot extends RealtimeSnapshotBase {
  type: 'theme_treemap_snapshot';
  topic: 'theme_treemap_snapshot';
  payload: {
    snapshotId: string;
    items: TreemapItem[];
  };
}

export interface RealtimeEventSnapshot extends RealtimeSnapshotBase {
  type: 'event_state_changed';
  topic: 'event_state_changed';
  payload: {
    eventId: string;
  } & Record<string, unknown>;
}

export type RealtimeSnapshot =
  | RealtimeRankingSnapshot
  | RealtimeTreemapSnapshot
  | RealtimeEventSnapshot;

export type RepositoryResource =
  | 'session'
  | 'rankings'
  | 'treemap'
  | 'detail'
  | 'evidence'
  | 'saved'
  | 'historical';

export interface ProductRepository {
  subscribe(resource: RepositoryResource, listener: () => void): () => void;
  getSession(): Promise<AuthSession>;
  startGoogleLogin(returnTo: string): Promise<AuthSession>;
  logout(): Promise<void>;
  getRankings(): Promise<RankingResponse>;
  getTreemap(): Promise<TreemapResponse>;
  getThemeDetail(themeId: string, eventId: string): Promise<ThemeDetailResponse>;
  getEvidence(eventId: string): Promise<EvidenceResponse>;
  getSaved(type: SavedType | 'ALL'): Promise<SavedResponse>;
  saveSaved(item: SavedTarget): Promise<void>;
  removeSaved(item: Pick<SavedItem, 'savedType' | 'targetId'>): Promise<void>;
  getHistoricalAccess(eventId: string): Promise<HistoricalAccessResponse['data']>;
}
