import type {
  AuthSession,
  CatalystDetailResponse,
  CatalystTop3Response,
  ResearchAnswerResponse,
  EvidenceResponse,
  HistoricalAccessResponse,
  HistoricalEventResponse,
  HistoricalHorizon,
  ProductRepository,
  RankingResponse,
  RealtimeRankingSnapshot,
  RealtimeSnapshot,
  RealtimeTicketResponse,
  RealtimeTopic,
  RealtimeTreemapSnapshot,
  RepositoryResource,
  SavedMutationResponse,
  SavedResponse,
  SavedTarget,
  SavedType,
  SessionResponse,
  SimilarEventsResponse,
  ThemeDetailResponse,
  TreemapResponse,
} from '../domain/contracts';
import { safeReturnTo } from '../domain/formatting';
import { RepositoryError } from '../domain/repositoryErrors';

const CSRF_COOKIE = '__Host-dayjaview_csrf';
const DEFAULT_REALTIME_URL = 'wss://api.dayjaview.duckdns.org/v1/realtime';

type Fetcher = (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;
type TimerHandle = ReturnType<typeof globalThis.setTimeout>;

export type RealtimeSocket = Pick<
  WebSocket,
  'onopen' | 'onmessage' | 'onclose' | 'onerror' | 'send' | 'close'
>;

export interface ProductionRepositoryOptions {
  fetcher?: Fetcher;
  webSocketFactory?: ((url: string) => RealtimeSocket) | null;
  realtimeUrl?: string;
  readCsrfToken?: () => string | null;
  random?: () => number;
  reconnectBaseMs?: number;
  reconnectMaxMs?: number;
  setTimer?: (callback: () => void, delayMs: number) => TimerHandle;
  clearTimer?: (handle: TimerHandle) => void;
}

interface ErrorPayload {
  error?: {
    code?: string;
    message?: string;
    retryable?: boolean;
  };
}

interface SequenceCursor {
  streamId: string;
  sequence: number;
}

function readCookie(name: string): string | null {
  const prefix = `${encodeURIComponent(name)}=`;
  const item = document.cookie
    .split(';')
    .map((value) => value.trim())
    .find((value) => value.startsWith(prefix));
  return item ? decodeURIComponent(item.slice(prefix.length)) : null;
}

function defaultWebSocketFactory(url: string): RealtimeSocket {
  return new WebSocket(url);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

function isRealtimeSnapshot(value: unknown): value is RealtimeSnapshot {
  if (!isRecord(value) || !isRecord(value.payload)) return false;
  if (value.type !== value.topic) return false;
  if (!['theme_rank_snapshot', 'theme_treemap_snapshot', 'event_state_changed'].includes(String(value.type))) {
    return false;
  }
  return (
    typeof value.streamId === 'string' &&
    typeof value.sequence === 'number' &&
    Number.isSafeInteger(value.sequence) &&
    value.sequence >= 0 &&
    typeof value.schemaVersion === 'string' &&
    typeof value.generatedAt === 'string' &&
    typeof value.asOf === 'string' &&
    typeof value.marketDate === 'string' &&
    typeof value.dataStatus === 'string' &&
    Array.isArray(value.qualityFlags)
  );
}

function savedPath(item: Pick<SavedTarget, 'savedType' | 'targetId'>): string {
  const collection = { THEME: 'themes', STOCK: 'stocks', EVENT: 'events' }[item.savedType];
  return `/api/v1/me/saved/${collection}/${encodeURIComponent(item.targetId)}`;
}

class LiveProductRepository implements ProductRepository {
  private readonly fetcher: Fetcher;
  private readonly webSocketFactory: ((url: string) => RealtimeSocket) | null;
  private readonly realtimeUrl: string;
  private readonly readCsrfToken: () => string | null;
  private readonly random: () => number;
  private readonly reconnectBaseMs: number;
  private readonly reconnectMaxMs: number;
  private readonly setTimer: (callback: () => void, delayMs: number) => TimerHandle;
  private readonly clearTimer: (handle: TimerHandle) => void;
  private readonly listeners = new Map<RepositoryResource, Set<() => void>>();
  private readonly detailCache = new Map<string, ThemeDetailResponse>();
  private readonly evidenceCache = new Map<string, EvidenceResponse>();
  private readonly savedCache = new Map<SavedType | 'ALL', SavedResponse>();
  private readonly sequenceByScope = new Map<string, SequenceCursor>();

  private authenticated: boolean | null = null;
  private rankingCache: RankingResponse | null = null;
  private treemapCache: TreemapResponse | null = null;
  private latestRankingSnapshot: RealtimeRankingSnapshot | null = null;
  private latestTreemapSnapshot: RealtimeTreemapSnapshot | null = null;
  private socket: RealtimeSocket | null = null;
  private connectionState: 'idle' | 'ticket' | 'connecting' | 'open' = 'idle';
  private connectionGeneration = 0;
  private reconnectAttempt = 0;
  private reconnectTimer: TimerHandle | null = null;
  private eventSubscriptionKey = 'event_state_changed:eventIds=';

  constructor(options: ProductionRepositoryOptions = {}) {
    this.fetcher = options.fetcher ?? fetch.bind(globalThis);
    this.webSocketFactory =
      options.webSocketFactory === undefined ? defaultWebSocketFactory : options.webSocketFactory;
    this.realtimeUrl = options.realtimeUrl ?? import.meta.env.VITE_REALTIME_URL ?? DEFAULT_REALTIME_URL;
    this.readCsrfToken = options.readCsrfToken ?? (() => readCookie(CSRF_COOKIE));
    this.random = options.random ?? Math.random;
    this.reconnectBaseMs = options.reconnectBaseMs ?? 500;
    this.reconnectMaxMs = options.reconnectMaxMs ?? 15_000;
    this.setTimer = options.setTimer ?? ((callback, delayMs) => globalThis.setTimeout(callback, delayMs));
    this.clearTimer = options.clearTimer ?? ((handle) => globalThis.clearTimeout(handle));
  }

  subscribe(resource: RepositoryResource, listener: () => void): () => void {
    const resourceListeners = this.listeners.get(resource) ?? new Set<() => void>();
    resourceListeners.add(listener);
    this.listeners.set(resource, resourceListeners);
    return () => resourceListeners.delete(listener);
  }

  private emit(resource: RepositoryResource) {
    this.listeners.get(resource)?.forEach((listener) => listener());
  }

  private csrfHeaders(): HeadersInit {
    const token = this.readCsrfToken();
    if (!token) {
      this.expireSession();
      throw new RepositoryError({
        kind: 'authentication',
        status: 401,
        code: 'AUTHENTICATION_REQUIRED',
        message: '로그인 보안 정보를 확인할 수 없습니다. 다시 로그인해 주세요.',
      });
    }
    return { 'X-CSRF-Token': token };
  }

  private async request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const headers = new Headers(init.headers);
    headers.set('Accept', 'application/json');
    let response: Response;

    try {
      response = await this.fetcher(path, {
        ...init,
        headers,
        credentials: 'include',
        cache: 'no-store',
      });
    } catch (error) {
      if (error instanceof RepositoryError) throw error;
      throw new RepositoryError({
        kind: 'network',
        message: '네트워크 연결이 원활하지 않습니다.',
        retryable: true,
      });
    }

    let payload: unknown = null;
    try {
      payload = await response.json();
    } catch {
      if (response.ok) {
        throw new RepositoryError({
          kind: 'contract',
          status: response.status,
          message: '서버 응답 형식을 확인할 수 없습니다.',
        });
      }
    }

    if (!response.ok) {
      const errorPayload = isRecord(payload) ? (payload as ErrorPayload) : null;
      const code = errorPayload?.error?.code ?? null;
      const retryable = errorPayload?.error?.retryable ?? response.status >= 500;

      if (response.status === 401) this.expireSession();

      const kind =
        response.status === 401
          ? 'authentication'
          : response.status === 403
            ? 'permission'
            : response.status >= 500
              ? 'unavailable'
              : 'contract';
      const message =
        response.status === 401
          ? '로그인이 만료되었습니다. 다시 로그인해 주세요.'
          : response.status === 403
            ? '현재 계정으로는 이 데이터에 접근할 수 없습니다.'
            : '요청한 데이터를 현재 제공할 수 없습니다.';
      throw new RepositoryError({ kind, status: response.status, code, message, retryable });
    }

    if (!isRecord(payload)) {
      throw new RepositoryError({
        kind: 'contract',
        status: response.status,
        message: '서버 응답 형식을 확인할 수 없습니다.',
      });
    }
    return payload as T;
  }

  private purgePrivateState() {
    this.rankingCache = null;
    this.treemapCache = null;
    this.latestRankingSnapshot = null;
    this.latestTreemapSnapshot = null;
    this.detailCache.clear();
    this.evidenceCache.clear();
    this.savedCache.clear();
    this.sequenceByScope.clear();
    this.closeRealtime();
  }

  private expireSession() {
    const shouldNotify = this.authenticated !== false;
    this.authenticated = false;
    this.purgePrivateState();
    if (shouldNotify) this.emit('session');
  }

  private closeRealtime() {
    this.connectionGeneration += 1;
    if (this.reconnectTimer !== null) {
      this.clearTimer(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    const socket = this.socket;
    this.socket = null;
    this.connectionState = 'idle';
    this.reconnectAttempt = 0;
    if (socket) socket.close(1000, 'session boundary');
  }

  async getSession(): Promise<AuthSession> {
    const response = await this.request<SessionResponse>('/api/auth/session');
    if (typeof response.data?.authenticated !== 'boolean') {
      throw new RepositoryError({
        kind: 'contract',
        message: '로그인 상태 응답 형식을 확인할 수 없습니다.',
      });
    }
    if (!response.data.authenticated) {
      this.authenticated = false;
      this.purgePrivateState();
    } else {
      this.authenticated = true;
    }
    return { authenticated: response.data.authenticated };
  }

  async startGoogleLogin(returnTo: string): Promise<AuthSession> {
    const target = safeReturnTo(returnTo);
    window.location.assign(`/api/auth/google?returnTo=${encodeURIComponent(target)}`);
    return new Promise<AuthSession>(() => undefined);
  }

  async logout(): Promise<void> {
    try {
      await this.request('/api/auth/logout', {
        method: 'POST',
        headers: this.csrfHeaders(),
      });
    } finally {
      const shouldNotify = this.authenticated !== false;
      this.authenticated = false;
      this.purgePrivateState();
      if (shouldNotify) this.emit('session');
    }
  }

  async getRankings(): Promise<RankingResponse> {
    if (!this.rankingCache) {
      const response = await this.request<RankingResponse>('/api/v1/themes/rankings?limit=10');
      this.rankingCache = response;
      this.seedSequence('theme_rank_snapshot', response.data.streamId, response.data.sequence);
      if (this.latestRankingSnapshot && this.acceptSnapshot(this.latestRankingSnapshot)) {
        this.applyRankingSnapshot(this.latestRankingSnapshot);
      }
    }
    this.startRealtime();
    return this.rankingCache;
  }

  async getTreemap(): Promise<TreemapResponse> {
    if (!this.treemapCache) {
      const response = await this.request<TreemapResponse>('/api/v1/insights/treemap?limit=12');
      this.treemapCache = response;
      this.seedSequence('theme_treemap_snapshot', response.data.streamId, response.data.sequence);
      if (this.latestTreemapSnapshot && this.acceptSnapshot(this.latestTreemapSnapshot)) {
        this.applyTreemapSnapshot(this.latestTreemapSnapshot);
      }
    }
    this.startRealtime();
    return this.treemapCache;
  }

  /**
   * 질문 문장은 URL에 싣지 않고 캐시하지도 않는다. 저장하지 않는 것이 계약이라
   * 여기서도 남기지 않는다.
   */
  async answerResearchQuestion(question: string): Promise<ResearchAnswerResponse> {
    return this.request<ResearchAnswerResponse>('/api/v1/research/answers', {
      method: 'POST',
      headers: { ...this.csrfHeaders(), 'Content-Type': 'application/json' },
      body: JSON.stringify({ question }),
    });
  }

  async getThemeDetail(themeId: string, eventId: string): Promise<ThemeDetailResponse> {
    const key = `${themeId}:${eventId}`;
    const cached = this.detailCache.get(key);
    if (cached) return cached;
    const response = await this.request<ThemeDetailResponse>(
      `/api/v1/themes/${encodeURIComponent(themeId)}/events/${encodeURIComponent(eventId)}`,
    );
    this.detailCache.set(key, response);
    return response;
  }

  async getEvidence(eventId: string, cursor?: string | null): Promise<EvidenceResponse> {
    const key = `${eventId}|${cursor ?? ''}`;
    const cached = this.evidenceCache.get(key);
    if (cached) return cached;
    const query = cursor ? `?limit=20&cursor=${encodeURIComponent(cursor)}` : '?limit=20';
    const response = await this.request<EvidenceResponse>(
      `/api/v1/events/${encodeURIComponent(eventId)}/evidence${query}`,
    );
    this.evidenceCache.set(key, response);
    return response;
  }

  async getSaved(type: SavedType | 'ALL'): Promise<SavedResponse> {
    const cached = this.savedCache.get(type);
    if (cached) return cached;
    const response = await this.request<SavedResponse>(
      `/api/v1/me/saved?type=${encodeURIComponent(type)}&limit=20`,
    );
    this.savedCache.set(type, response);
    return response;
  }

  async saveSaved(item: SavedTarget): Promise<void> {
    const response = await this.request<SavedMutationResponse>(savedPath(item), {
      method: 'PUT',
      headers: this.csrfHeaders(),
    });
    this.assertSavedMutation(response, item, true);
    this.savedCache.clear();
    this.emit('saved');
  }

  async removeSaved(item: Pick<SavedTarget, 'savedType' | 'targetId'>): Promise<void> {
    const response = await this.request<SavedMutationResponse>(savedPath(item), {
      method: 'DELETE',
      headers: this.csrfHeaders(),
    });
    this.assertSavedMutation(response, item, false);
    this.savedCache.clear();
    this.emit('saved');
  }

  private assertSavedMutation(
    response: SavedMutationResponse,
    item: Pick<SavedTarget, 'savedType' | 'targetId'>,
    expectedSaved: boolean,
  ) {
    if (
      response.data?.savedType !== item.savedType ||
      response.data?.targetId !== item.targetId ||
      response.data?.saved !== expectedSaved
    ) {
      throw new RepositoryError({
        kind: 'contract',
        message: '저장 응답의 대상이 요청과 일치하지 않습니다.',
      });
    }
  }

  async getHistoricalAccess(eventId: string): Promise<HistoricalAccessResponse['data']> {
    return { eventId, availability: 'GATED' };
  }

  async getSimilarEvents(
    eventId: string,
    horizon: HistoricalHorizon,
  ): Promise<SimilarEventsResponse> {
    // 미래 결과로 관련성 순서를 바꾸지 않는다. sort=outcome은 제공되지 않는다 (api_contract 10.2).
    return this.request<SimilarEventsResponse>(
      `/api/v1/events/${encodeURIComponent(eventId)}/similar-events` +
        `?horizonTradingDays=${horizon}&sort=relevance&limit=20`,
    );
  }

  async getHistoricalEvent(
    matchedEventId: string,
    contextEventId?: string | null,
  ): Promise<HistoricalEventResponse> {
    const query = contextEventId
      ? `?contextEventId=${encodeURIComponent(contextEventId)}`
      : '';
    return this.request<HistoricalEventResponse>(
      `/api/v1/events/${encodeURIComponent(matchedEventId)}${query}`,
    );
  }

  async getCatalystDetail(): Promise<CatalystDetailResponse> {
    // api_contract에 대응 endpoint가 없다(배선 매핑표 §5.1). 없는 주소를 호출하지 않고 미제공으로 닫는다.
    throw new RepositoryError({
      kind: 'permission',
      message: '과거 소재 유형 상세는 아직 제공되지 않습니다.',
    });
  }

  getCachedRank(eventId: string): number | null {
    return this.rankingCache?.data.items.find((item) => item.eventId === eventId)?.rank ?? null;
  }

  async getCatalystTop3(): Promise<CatalystTop3Response> {
    throw new RepositoryError({
      kind: 'permission',
      message: '과거 상승 소재 TOP3는 아직 제공되지 않습니다.',
    });
  }

  private sequenceKey(topic: RealtimeTopic): string {
    if (topic === 'theme_rank_snapshot') return 'theme_rank_snapshot:limit=10';
    if (topic === 'theme_treemap_snapshot') return 'theme_treemap_snapshot:limit=12';
    return this.eventSubscriptionKey;
  }

  private seedSequence(topic: RealtimeTopic, streamId: string, sequence: number) {
    this.sequenceByScope.set(this.sequenceKey(topic), { streamId, sequence });
  }

  private acceptSnapshot(snapshot: RealtimeSnapshot): boolean {
    const key = this.sequenceKey(snapshot.topic);
    const previous = this.sequenceByScope.get(key);
    if (
      previous &&
      previous.streamId === snapshot.streamId &&
      snapshot.sequence <= previous.sequence
    ) {
      return false;
    }
    this.sequenceByScope.set(key, {
      streamId: snapshot.streamId,
      sequence: snapshot.sequence,
    });
    return true;
  }

  private applyRankingSnapshot(snapshot: RealtimeRankingSnapshot) {
    this.latestRankingSnapshot = snapshot;
    const previous = this.rankingCache;
    if (!previous) return;
    const lastHealthyAt =
      snapshot.dataStatus === 'LIVE' ? snapshot.asOf : previous.meta.marketContext.lastHealthyAt;
    this.rankingCache = {
      data: {
        snapshotId: snapshot.payload.snapshotId,
        streamId: snapshot.streamId,
        sequence: snapshot.sequence,
        items: snapshot.payload.items,
      },
      meta: {
        ...previous.meta,
        schemaVersion: snapshot.schemaVersion,
        generatedAt: snapshot.generatedAt,
        marketContext: {
          ...previous.meta.marketContext,
          marketDate: snapshot.marketDate,
          asOf: snapshot.asOf,
          dataStatus: snapshot.dataStatus,
          lastHealthyAt,
          qualityFlags: snapshot.qualityFlags,
        },
      },
    };
    this.emit('rankings');
  }

  private applyTreemapSnapshot(snapshot: RealtimeTreemapSnapshot) {
    this.latestTreemapSnapshot = snapshot;
    const previous = this.treemapCache;
    if (!previous) return;
    const lastHealthyAt =
      snapshot.dataStatus === 'LIVE' ? snapshot.asOf : previous.meta.marketContext.lastHealthyAt;
    this.treemapCache = {
      data: {
        snapshotId: snapshot.payload.snapshotId,
        streamId: snapshot.streamId,
        sequence: snapshot.sequence,
        items: snapshot.payload.items,
      },
      meta: {
        ...previous.meta,
        schemaVersion: snapshot.schemaVersion,
        generatedAt: snapshot.generatedAt,
        marketContext: {
          ...previous.meta.marketContext,
          marketDate: snapshot.marketDate,
          asOf: snapshot.asOf,
          dataStatus: snapshot.dataStatus,
          lastHealthyAt,
          qualityFlags: snapshot.qualityFlags,
        },
      },
    };
    this.emit('treemap');
  }

  private markRealtimeDelayed() {
    if (this.rankingCache?.meta.marketContext.dataStatus === 'LIVE') {
      this.rankingCache = {
        ...this.rankingCache,
        meta: {
          ...this.rankingCache.meta,
          marketContext: {
            ...this.rankingCache.meta.marketContext,
            dataStatus: 'DELAYED',
            lastHealthyAt:
              this.rankingCache.meta.marketContext.lastHealthyAt ??
              this.rankingCache.meta.marketContext.asOf,
          },
        },
      };
      this.emit('rankings');
    }
    if (this.treemapCache?.meta.marketContext.dataStatus === 'LIVE') {
      this.treemapCache = {
        ...this.treemapCache,
        meta: {
          ...this.treemapCache.meta,
          marketContext: {
            ...this.treemapCache.meta.marketContext,
            dataStatus: 'DELAYED',
            lastHealthyAt:
              this.treemapCache.meta.marketContext.lastHealthyAt ??
              this.treemapCache.meta.marketContext.asOf,
          },
        },
      };
      this.emit('treemap');
    }
  }

  private startRealtime() {
    if (
      !this.webSocketFactory ||
      this.authenticated !== true ||
      this.connectionState !== 'idle' ||
      this.reconnectTimer !== null
    ) {
      return;
    }
    void this.connectRealtime().catch((error: unknown) => {
      if (this.connectionState === 'ticket') this.connectionState = 'idle';
      if (error instanceof RepositoryError && error.kind === 'authentication') return;
      this.markRealtimeDelayed();
      this.scheduleReconnect();
    });
  }

  private async connectRealtime() {
    this.connectionState = 'ticket';
    const generation = ++this.connectionGeneration;
    const ticket = await this.request<RealtimeTicketResponse>('/api/v1/auth/realtime-ticket', {
      method: 'POST',
      headers: this.csrfHeaders(),
    });
    if (generation !== this.connectionGeneration || this.authenticated !== true) return;
    if (typeof ticket.data?.ticket !== 'string' || !ticket.data.ticket) {
      throw new RepositoryError({
        kind: 'contract',
        message: '실시간 연결 ticket 응답 형식을 확인할 수 없습니다.',
      });
    }

    const socket = this.webSocketFactory?.(this.realtimeUrl);
    if (!socket) return;
    this.socket = socket;
    this.connectionState = 'connecting';

    socket.onopen = () => {
      if (this.socket !== socket || generation !== this.connectionGeneration) return;
      this.connectionState = 'open';
      socket.send(JSON.stringify({ type: 'auth', ticket: ticket.data.ticket }));
      socket.send(JSON.stringify(this.subscriptionRequest(generation)));
    };
    socket.onmessage = (event) => {
      if (this.socket !== socket) return;
      this.handleRealtimeMessage(event.data, socket);
    };
    socket.onerror = () => {
      if (this.socket === socket) socket.close(1011, 'realtime transport error');
    };
    socket.onclose = () => {
      if (this.socket !== socket) return;
      this.socket = null;
      this.connectionState = 'idle';
      this.sequenceByScope.clear();
      this.markRealtimeDelayed();
      this.scheduleReconnect();
    };
  }

  private subscriptionRequest(generation: number) {
    const eventIds = [...new Set(this.rankingCache?.data.items.map((item) => item.eventId) ?? [])]
      .sort()
      .slice(0, 50);
    this.eventSubscriptionKey = `event_state_changed:eventIds=${eventIds.join(',')}`;
    const topics: Array<{ name: RealtimeTopic; params: Record<string, unknown> }> = [
      { name: 'theme_rank_snapshot', params: { limit: 10 } },
      { name: 'theme_treemap_snapshot', params: { limit: 12 } },
    ];
    if (eventIds.length) {
      topics.push({ name: 'event_state_changed', params: { eventIds } });
    }
    return {
      type: 'subscribe',
      requestId: `client_${generation.toLocaleString('en-US', { useGrouping: false })}`,
      topics,
    };
  }

  private handleRealtimeMessage(raw: unknown, socket: RealtimeSocket) {
    let message: unknown;
    try {
      message = JSON.parse(String(raw));
    } catch {
      return;
    }
    if (!isRecord(message)) return;

    if (message.type === 'ping' && typeof message.sentAt === 'string') {
      socket.send(JSON.stringify({ type: 'pong', sentAt: message.sentAt }));
      return;
    }
    if (message.type === 'error') {
      if (message.code === 'AUTHENTICATION_REQUIRED') this.expireSession();
      else if (message.retryable === true) socket.close(1012, 'retryable realtime error');
      return;
    }
    if (!isRealtimeSnapshot(message) || !this.acceptSnapshot(message)) return;

    this.reconnectAttempt = 0;
    if (message.topic === 'theme_rank_snapshot') this.applyRankingSnapshot(message);
    if (message.topic === 'theme_treemap_snapshot') this.applyTreemapSnapshot(message);
    if (message.topic === 'event_state_changed') {
      for (const key of this.detailCache.keys()) {
        if (key.endsWith(`:${message.payload.eventId}`)) this.detailCache.delete(key);
      }
      for (const key of this.evidenceCache.keys()) {
        if (key.startsWith(`${message.payload.eventId}|`)) this.evidenceCache.delete(key);
      }
      this.emit('detail');
      this.emit('evidence');
    }
  }

  private scheduleReconnect() {
    if (
      this.authenticated !== true ||
      !this.webSocketFactory ||
      this.reconnectTimer !== null
    ) {
      return;
    }
    const exponential = Math.min(
      this.reconnectMaxMs,
      this.reconnectBaseMs * 2 ** this.reconnectAttempt,
    );
    const jitter = exponential * 0.2 * this.random();
    this.reconnectAttempt += 1;
    this.reconnectTimer = this.setTimer(() => {
      this.reconnectTimer = null;
      this.startRealtime();
    }, exponential + jitter);
  }
}

export function createProductionRepository(
  options: ProductionRepositoryOptions = {},
): ProductRepository {
  return new LiveProductRepository(options);
}

export type { SavedType };
