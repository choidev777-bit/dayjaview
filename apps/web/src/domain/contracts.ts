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
      /** 가격을 확인하지 못한 종목은 null이다. 0으로 바꾸지 않는다 (stage0 Leader 스키마). */
      return: number | null;
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

export type HistoricalHorizon = 1 | 5 | 20;
export type HistoricalAvailability = 'AVAILABLE' | 'GATED' | 'UNAVAILABLE';

/** 관찰이 끝나지 않은 기간은 PENDING이다. 결측(UNAVAILABLE)과 구분해서 표시한다 (screen_spec 10.5). */
export interface HistoricalOutcome {
  horizonTradingDays: HistoricalHorizon;
  return: number | null;
  status: 'OBSERVED' | 'UNAVAILABLE' | 'PENDING';
  unavailableReason: string | null;
}

/** 기간마다 유효 분모가 다르다. 하나로 합쳐 표시하지 않는다 (screen_spec 8.8·9.2). */
export interface HistoricalSummary {
  horizonTradingDays: HistoricalHorizon;
  eligibleCount: number;
  observedCount: number;
  positiveCount: number;
  medianReturn: number | null;
}

export interface SimilarEventItem {
  matchedEventId: string;
  marketDate: string;
  displayNameAtEvent: string;
  normalizedCatalystSummary: string;
  similarityReasons: string[];
  outcomes: HistoricalOutcome[];
}

export interface SimilarEventsResponse {
  data: {
    eventId: string;
    decisionAt: string;
    availability: HistoricalAvailability;
    summary: HistoricalSummary[];
    items: SimilarEventItem[];
    page: {
      nextCursor: string | null;
      hasMore: boolean;
      limit: number;
    };
  };
  meta: ResponseMeta;
}

export interface HistoricalEventResponse {
  data: {
    eventId: string;
    marketDate: string;
    displayNameAtEvent: string;
    catalystSummary: string;
    similarityReasons: string[] | null;
    leaders: Array<{
      stockId: string;
      symbol: string;
      name: string;
      /** 가격을 확인하지 못한 종목은 null이다. 0으로 바꾸지 않는다 (stage0 Leader 스키마). */
      return: number | null;
      role: 'LEADER';
    }>;
    outcomes: HistoricalOutcome[];
    futureOutcomeExcludedFromSelection: true;
  };
  meta: ResponseMeta;
}

/**
 * 과거 소재 유형 상세. `api_contract.md`에 대응 endpoint가 없어(배선 매핑표 §5.1 미해결 갭)
 * 서버 계약이 아니라 화면 검토용 형태다. 표현은 screen_spec 8.7·11.1을 따른다 —
 * 상승 빈도를 확률·성공률로 바꾸지 않고 유효 표본 수와 중앙 반응을 쓴다.
 */
export interface CatalystTop3Response {
  data: {
    themeId: string;
    eventId: string;
    items: Array<{
      catalystId: string;
      catalystName: string;
      eligibleCount: number;
      observedCount: number;
      medianSameDayReturn: number | null;
      /** 오늘과 같은 유형이라는 표시일 뿐, 같은 수익률이나 상승을 뜻하지 않는다 (screen_spec 8.7). */
      matchesToday: boolean;
    }>;
    qualityNote: string | null;
  };
  meta: ResponseMeta;
}

export interface CatalystDetailResponse {
  data: {
    catalystId: string;
    themeId: string;
    themeDisplayName: string;
    catalystName: string;
    availability: HistoricalAvailability;
    sameDay: HistoricalSummary;
    horizons: HistoricalSummary[];
    events: Array<{
      matchedEventId: string;
      marketDate: string;
      normalizedCatalystSummary: string;
      sameDayReturn: number | null;
      leaderName: string | null;
    }>;
    qualityNote: string | null;
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
  getEvidence(eventId: string, cursor?: string | null): Promise<EvidenceResponse>;
  getSaved(type: SavedType | 'ALL'): Promise<SavedResponse>;
  saveSaved(item: SavedTarget): Promise<void>;
  removeSaved(item: Pick<SavedItem, 'savedType' | 'targetId'>): Promise<void>;
  getHistoricalAccess(eventId: string): Promise<HistoricalAccessResponse['data']>;
  getSimilarEvents(eventId: string, horizon: HistoricalHorizon): Promise<SimilarEventsResponse>;
  getHistoricalEvent(
    matchedEventId: string,
    contextEventId?: string | null,
  ): Promise<HistoricalEventResponse>;
  getCatalystDetail(catalystId: string): Promise<CatalystDetailResponse>;
  getCatalystTop3(themeId: string, eventId: string): Promise<CatalystTop3Response>;
  /**
   * 이번 세션에서 이미 받아둔 순위 응답에서 해당 Event의 순위를 꺼낸다.
   * 테마 상세 응답에는 rank가 없어서 서버를 다시 부르지 않고 재사용한다.
   * 목록을 거치지 않고 URL로 바로 들어온 경우에는 null이고, 그때는 뱃지를 숨긴다.
   */
  getCachedRank(eventId: string): number | null;
}
