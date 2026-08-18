import rankingUnavailableFixture from '../../../../contracts/fixtures/rankings/calculation-unavailable.json';
import rankingLiveFixture from '../../../../contracts/fixtures/rankings/live.json';
import treemapLiveFixture from '../../../../contracts/fixtures/treemap/live.json';
import { describe, expect, it, vi } from 'vitest';
import { createFixtureRepository } from '../adapters/fixtureRepository';
import {
  createProductionRepository,
  type RealtimeSocket,
} from '../adapters/productionRepository';
import type {
  RankingResponse,
  RealtimeRankingSnapshot,
  RealtimeTreemapSnapshot,
  ResponseMeta,
  SavedItem,
  SavedMutationResponse,
  SavedResponse,
  SessionResponse,
  TreemapResponse,
} from '../domain/contracts';
import { RepositoryError } from '../domain/repositoryErrors';

const rankingLive = rankingLiveFixture as unknown as RankingResponse;
const rankingUnavailable = rankingUnavailableFixture as unknown as RankingResponse;
const treemapLive = treemapLiveFixture as unknown as TreemapResponse;

const meta: ResponseMeta = {
  requestId: 'req_test',
  apiVersion: '1',
  schemaVersion: '2026-08-14.1',
  generatedAt: '2026-08-14T01:18:23.042Z',
};

const authenticatedSession: SessionResponse = {
  data: {
    authenticated: true,
    user: { displayName: '테스트 사용자' },
    roles: ['USER'],
  },
  meta,
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function errorResponse(status: number, code: string): Response {
  return jsonResponse(
    {
      error: { code, message: '계약 오류', retryable: status >= 500, details: {} },
      meta,
    },
    status,
  );
}

function realtimeTicket(ticket = 'rt_test') {
  return {
    data: { ticket, expiresAt: '2026-08-14T01:18:53.042Z' },
    meta,
  };
}

class FakeSocket implements RealtimeSocket {
  onopen: WebSocket['onopen'] = null;
  onmessage: WebSocket['onmessage'] = null;
  onclose: WebSocket['onclose'] = null;
  onerror: WebSocket['onerror'] = null;
  readonly sent: string[] = [];
  closed = false;

  send(data: string | ArrayBufferLike | Blob | ArrayBufferView) {
    this.sent.push(String(data));
  }

  close() {
    if (this.closed) return;
    this.closed = true;
    this.onclose?.call(this as unknown as WebSocket, new CloseEvent('close'));
  }

  serverOpen() {
    this.onopen?.call(this as unknown as WebSocket, new Event('open'));
  }

  serverMessage(message: unknown) {
    this.onmessage?.call(
      this as unknown as WebSocket,
      new MessageEvent('message', { data: JSON.stringify(message) }),
    );
  }

  serverClose() {
    this.close();
  }
}

function rankingSnapshot({
  sequence,
  snapshotId,
  displayName,
  weightedReturn = 0.027,
  streamId = rankingLive.data.streamId,
}: {
  sequence: number;
  snapshotId: string;
  displayName: string;
  weightedReturn?: number | null;
  streamId?: string;
}): RealtimeRankingSnapshot {
  const item = structuredClone(rankingLive.data.items[0]);
  item.classification.displayName = displayName;
  item.weightedReturn = weightedReturn;
  return {
    type: 'theme_rank_snapshot',
    schemaVersion: meta.schemaVersion,
    subscriptionId: 'sub_test',
    streamId,
    topic: 'theme_rank_snapshot',
    sequence,
    generatedAt: meta.generatedAt,
    asOf: rankingLive.meta.marketContext.asOf,
    marketDate: rankingLive.meta.marketContext.marketDate,
    dataStatus: 'LIVE',
    qualityFlags: [],
    payload: { snapshotId, items: [item] },
  };
}

function treemapSnapshot(
  sequence: number,
  snapshotId: string,
  streamId = treemapLive.data.streamId,
): RealtimeTreemapSnapshot {
  return {
    type: 'theme_treemap_snapshot',
    schemaVersion: meta.schemaVersion,
    subscriptionId: 'sub_test',
    streamId,
    topic: 'theme_treemap_snapshot',
    sequence,
    generatedAt: meta.generatedAt,
    asOf: treemapLive.meta.marketContext.asOf,
    marketDate: treemapLive.meta.marketContext.marketDate,
    dataStatus: 'LIVE',
    qualityFlags: [],
    payload: { snapshotId, items: structuredClone(treemapLive.data.items) },
  };
}

describe('live REST adapter 인증·cache 경계', () => {
  it('fixture와 같은 계약 응답을 그대로 소비하고 null·실제 0·Coverage를 추정하지 않는다', async () => {
    const fixture = createFixtureRepository({ ranking: 'unavailable' });
    const live = createProductionRepository({
      webSocketFactory: null,
      fetcher: async (input) => {
        if (String(input) === '/api/auth/session') return jsonResponse(authenticatedSession);
        return jsonResponse(rankingUnavailable);
      },
    });

    await live.getSession();
    const fixtureResponse = await fixture.getRankings();
    const liveResponse = await live.getRankings();
    expect(liveResponse).toEqual(fixtureResponse);
    expect(liveResponse.data.items[0].weightedReturn).toBeNull();
    expect(liveResponse.data.items[0].coverage.status).toBe('INSUFFICIENT');
    expect(liveResponse.data.items[0].coverage.core.countRatio).toBeNull();

    const actualZero = structuredClone(rankingLive);
    actualZero.data.items[0].weightedReturn = 0;
    const zeroRepository = createProductionRepository({
      webSocketFactory: null,
      fetcher: async () => jsonResponse(actualZero),
    });
    expect((await zeroRepository.getRankings()).data.items[0].weightedReturn).toBe(0);
  });

  it('401에서 session을 만료시키고 사용자별 cache를 폐기한 뒤 재로그인 시 REST를 다시 조회한다', async () => {
    let rankingReads = 0;
    let sessionReads = 0;
    const sessionChanged = vi.fn();
    const repository = createProductionRepository({
      webSocketFactory: null,
      fetcher: async (input) => {
        const path = String(input);
        if (path === '/api/auth/session') {
          sessionReads += 1;
          return jsonResponse(authenticatedSession);
        }
        if (path.startsWith('/api/v1/themes/rankings')) {
          rankingReads += 1;
          const response = structuredClone(rankingLive);
          response.data.snapshotId = `snap_${rankingReads}`;
          return jsonResponse(response);
        }
        if (path.startsWith('/api/v1/me/saved')) {
          return errorResponse(401, 'AUTHENTICATION_REQUIRED');
        }
        throw new Error(`예상하지 못한 요청: ${path}`);
      },
    });
    repository.subscribe('session', sessionChanged);

    await repository.getSession();
    expect((await repository.getRankings()).data.snapshotId).toBe('snap_1');
    expect((await repository.getRankings()).data.snapshotId).toBe('snap_1');
    await expect(repository.getSaved('ALL')).rejects.toMatchObject({
      kind: 'authentication',
      status: 401,
    });
    expect(sessionChanged).toHaveBeenCalledTimes(1);

    await repository.getSession();
    expect((await repository.getRankings()).data.snapshotId).toBe('snap_2');
    expect(rankingReads).toBe(2);
    expect(sessionReads).toBe(2);
  });

  it('403을 session 만료와 구분하고 권한 오류로 유지한다', async () => {
    const sessionChanged = vi.fn();
    const repository = createProductionRepository({
      webSocketFactory: null,
      fetcher: async (input) =>
        String(input) === '/api/auth/session'
          ? jsonResponse(authenticatedSession)
          : errorResponse(403, 'FEATURE_NOT_ENTITLED'),
    });
    repository.subscribe('session', sessionChanged);
    await repository.getSession();

    await expect(repository.getSaved('EVENT')).rejects.toMatchObject({
      kind: 'permission',
      status: 403,
    });
    expect(sessionChanged).not.toHaveBeenCalled();
  });

  it('logout 성공 시 socket 여부와 무관하게 사용자 cache를 폐기한다', async () => {
    let rankingReads = 0;
    const repository = createProductionRepository({
      webSocketFactory: null,
      readCsrfToken: () => 'csrf_test',
      fetcher: async (input, init) => {
        const path = String(input);
        if (path === '/api/auth/session') return jsonResponse(authenticatedSession);
        if (path === '/api/auth/logout' && init?.method === 'POST') {
          return jsonResponse({ data: { loggedOut: true }, meta });
        }
        if (path.startsWith('/api/v1/themes/rankings')) {
          rankingReads += 1;
          const response = structuredClone(rankingLive);
          response.data.snapshotId = `logout_boundary_${rankingReads}`;
          return jsonResponse(response);
        }
        throw new Error(`예상하지 못한 요청: ${path}`);
      },
    });

    await repository.getSession();
    expect((await repository.getRankings()).data.snapshotId).toBe('logout_boundary_1');
    await repository.logout();
    await repository.getSession();
    expect((await repository.getRankings()).data.snapshotId).toBe('logout_boundary_2');
    expect(rankingReads).toBe(2);
  });
});

describe('live saved adapter 동기화·IDOR 경계', () => {
  it('userId 없이 PUT·DELETE하고 mutation 뒤 서버 목록을 다시 동기화한다', async () => {
    const calls: Array<{ path: string; init?: RequestInit }> = [];
    let listRead = 0;
    const savedItem: SavedItem = {
      savedType: 'THEME',
      targetId: 'thm_nuclear',
      displayName: '원전수출',
      savedAt: meta.generatedAt,
      availability: 'AVAILABLE',
      unavailableReason: null,
      currentState: null,
    };
    const savedList = (items: SavedItem[]): SavedResponse => ({ data: { items }, meta });
    const mutation = (saved: boolean): SavedMutationResponse => ({
      data: {
        savedType: 'THEME',
        targetId: 'thm_nuclear',
        saved,
        savedAt: saved ? meta.generatedAt : null,
      },
      meta,
    });
    const repository = createProductionRepository({
      webSocketFactory: null,
      readCsrfToken: () => 'csrf_test',
      fetcher: async (input, init) => {
        const path = String(input);
        calls.push({ path, init });
        if (path === '/api/auth/session') return jsonResponse(authenticatedSession);
        if (path.startsWith('/api/v1/me/saved?')) {
          listRead += 1;
          return jsonResponse(savedList(listRead === 2 ? [savedItem] : []));
        }
        if (init?.method === 'PUT') return jsonResponse(mutation(true));
        if (init?.method === 'DELETE') return jsonResponse(mutation(false));
        throw new Error(`예상하지 못한 요청: ${path}`);
      },
    });

    await repository.getSession();
    expect((await repository.getSaved('THEME')).data.items).toEqual([]);
    await repository.saveSaved({
      savedType: 'THEME',
      targetId: 'thm_nuclear',
      displayName: '클라이언트 표시값은 전송하지 않음',
    });
    expect((await repository.getSaved('THEME')).data.items).toEqual([savedItem]);
    await repository.removeSaved({ savedType: 'THEME', targetId: 'thm_nuclear' });
    expect((await repository.getSaved('THEME')).data.items).toEqual([]);

    const mutations = calls.filter((call) => call.init?.method === 'PUT' || call.init?.method === 'DELETE');
    expect(mutations.map((call) => [call.init?.method, call.path])).toEqual([
      ['PUT', '/api/v1/me/saved/themes/thm_nuclear'],
      ['DELETE', '/api/v1/me/saved/themes/thm_nuclear'],
    ]);
    for (const call of mutations) {
      const headers = new Headers(call.init?.headers);
      expect(headers.get('X-CSRF-Token')).toBe('csrf_test');
      expect(headers.has('Origin')).toBe(false);
      expect(call.path).not.toContain('userId');
      expect(call.init?.body).toBeUndefined();
    }
  });

  it('서버 mutation의 type·targetId·최종 상태가 다르면 cache를 갱신하지 않는다', async () => {
    let listReads = 0;
    const repository = createProductionRepository({
      webSocketFactory: null,
      readCsrfToken: () => 'csrf_test',
      fetcher: async (input, init) => {
        if (String(input).startsWith('/api/v1/me/saved?')) {
          listReads += 1;
          return jsonResponse({ data: { items: [] }, meta });
        }
        if (init?.method === 'PUT') {
          return jsonResponse({
            data: { savedType: 'THEME', targetId: 'thm_other', saved: true, savedAt: meta.generatedAt },
            meta,
          });
        }
        return jsonResponse(authenticatedSession);
      },
    });
    await repository.getSaved('THEME');

    await expect(
      repository.saveSaved({ savedType: 'THEME', targetId: 'thm_nuclear' }),
    ).rejects.toBeInstanceOf(RepositoryError);
    await repository.getSaved('THEME');
    expect(listReads).toBe(1);
  });
});

describe('live WebSocket full snapshot·sequence·reconnect', () => {
  it.each([
    {
      caseName: 'same stream older buffered snapshot',
      bufferedSequence: treemapLive.data.sequence - 1,
      bufferedStreamId: treemapLive.data.streamId,
      usesBufferedSnapshot: false,
    },
    {
      caseName: 'same stream equal buffered snapshot',
      bufferedSequence: treemapLive.data.sequence,
      bufferedStreamId: treemapLive.data.streamId,
      usesBufferedSnapshot: false,
    },
    {
      caseName: 'same stream newer buffered snapshot',
      bufferedSequence: treemapLive.data.sequence + 1,
      bufferedStreamId: treemapLive.data.streamId,
      usesBufferedSnapshot: true,
    },
    {
      caseName: 'new stream first buffered full snapshot',
      bufferedSequence: 1,
      bufferedStreamId: 'stream_market_20260815',
      usesBufferedSnapshot: true,
    },
  ])(
    'reconciles $caseName against the first treemap REST response',
    async ({ bufferedSequence, bufferedStreamId, usesBufferedSnapshot }) => {
      const sockets: FakeSocket[] = [];
      let resolveTreemapResponse!: (response: Response) => void;
      const treemapResponse = new Promise<Response>((resolve) => {
        resolveTreemapResponse = resolve;
      });
      const repository = createProductionRepository({
        readCsrfToken: () => 'csrf_test',
        webSocketFactory: () => {
          const socket = new FakeSocket();
          sockets.push(socket);
          return socket;
        },
        fetcher: async (input) => {
          const path = String(input);
          if (path === '/api/auth/session') return jsonResponse(authenticatedSession);
          if (path.startsWith('/api/v1/themes/rankings')) return jsonResponse(rankingLive);
          if (path.startsWith('/api/v1/insights/treemap')) return treemapResponse;
          if (path === '/api/v1/auth/realtime-ticket') {
            return jsonResponse(realtimeTicket());
          }
          throw new Error(`Unexpected request: ${path}`);
        },
      });

      await repository.getSession();
      await repository.getRankings();
      await vi.waitFor(() => expect(sockets).toHaveLength(1));
      sockets[0].serverOpen();

      const pendingTreemap = repository.getTreemap();

      const bufferedSnapshotId = `buffered_${bufferedStreamId}_${bufferedSequence}`;
      const bufferedDisplayName = `WS ${bufferedSnapshotId}`;
      const bufferedSnapshot = treemapSnapshot(
        bufferedSequence,
        bufferedSnapshotId,
        bufferedStreamId,
      );
      bufferedSnapshot.payload.items[0].displayName = bufferedDisplayName;
      sockets[0].serverMessage(bufferedSnapshot);
      resolveTreemapResponse(jsonResponse(treemapLive));

      const result = await pendingTreemap;
      expect(result.data.snapshotId).toBe(
        usesBufferedSnapshot ? bufferedSnapshotId : treemapLive.data.snapshotId,
      );
      expect(result.data.streamId).toBe(
        usesBufferedSnapshot ? bufferedStreamId : treemapLive.data.streamId,
      );
      expect(result.data.sequence).toBe(
        usesBufferedSnapshot ? bufferedSequence : treemapLive.data.sequence,
      );
      expect(result.data.items[0].displayName).toBe(
        usesBufferedSnapshot ? bufferedDisplayName : treemapLive.data.items[0].displayName,
      );
    },
  );

  it('rankings 스냅샷마다 상세 캐시를 비우고, 근거 캐시는 30스냅샷마다 비운다 (C-13)', async () => {
    const sockets: FakeSocket[] = [];
    let detailFetches = 0;
    let evidenceFetches = 0;
    const detailBody = { data: { themeId: 'thm_nuclear', eventId: 'evt_current' }, meta };
    const evidenceBody = {
      data: { evidenceStatus: 'SEARCHING', items: [], page: { hasMore: false, nextCursor: null } },
      meta,
    };
    const repository = createProductionRepository({
      readCsrfToken: () => 'csrf_test',
      webSocketFactory: () => {
        const socket = new FakeSocket();
        sockets.push(socket);
        return socket;
      },
      fetcher: async (input) => {
        const path = String(input);
        if (path === '/api/auth/session') return jsonResponse(authenticatedSession);
        if (path.startsWith('/api/v1/themes/rankings')) return jsonResponse(rankingLive);
        if (path === '/api/v1/auth/realtime-ticket') return jsonResponse(realtimeTicket());
        if (path.startsWith('/api/v1/themes/thm_nuclear/events/evt_current')) {
          detailFetches += 1;
          return jsonResponse(detailBody);
        }
        if (path.startsWith('/api/v1/events/evt_current/evidence')) {
          evidenceFetches += 1;
          return jsonResponse(evidenceBody);
        }
        throw new Error(`Unexpected request: ${path}`);
      },
    });

    await repository.getSession();
    await repository.getRankings();
    await vi.waitFor(() => expect(sockets).toHaveLength(1));
    sockets[0].serverOpen();

    const detailRefreshes: number[] = [];
    const evidenceRefreshes: number[] = [];
    repository.subscribe('detail', () => detailRefreshes.push(detailFetches));
    repository.subscribe('evidence', () => evidenceRefreshes.push(evidenceFetches));

    await repository.getThemeDetail('thm_nuclear', 'evt_current');
    await repository.getThemeDetail('thm_nuclear', 'evt_current');
    await repository.getEvidence('evt_current');
    expect(detailFetches).toBe(1);
    expect(evidenceFetches).toBe(1);

    let sequence = rankingLive.data.sequence;
    sockets[0].serverMessage(
      rankingSnapshot({ sequence: (sequence += 1), snapshotId: 'snap_c13_1', displayName: 'C13' }),
    );
    expect(detailRefreshes).toHaveLength(1);
    expect(evidenceRefreshes).toHaveLength(0);
    await repository.getThemeDetail('thm_nuclear', 'evt_current');
    await repository.getEvidence('evt_current');
    expect(detailFetches).toBe(2);
    expect(evidenceFetches).toBe(1);

    for (let i = 0; i < 29; i += 1) {
      sockets[0].serverMessage(
        rankingSnapshot({
          sequence: (sequence += 1),
          snapshotId: `snap_c13_${i + 2}`,
          displayName: 'C13',
        }),
      );
    }
    expect(evidenceRefreshes).toHaveLength(1);
    await repository.getEvidence('evt_current');
    expect(evidenceFetches).toBe(2);
  });

  it('topic별 sequence를 추적해 중복·역순을 무시하고 gap과 재연결 첫 full snapshot으로 교체한다', async () => {
    const sockets: FakeSocket[] = [];
    const reconnectCallbacks: Array<() => void> = [];
    const repository = createProductionRepository({
      readCsrfToken: () => 'csrf_test',
      random: () => 0,
      reconnectBaseMs: 1,
      webSocketFactory: (url) => {
        expect(url).toBe('wss://api.dayjaview.duckdns.org/v1/realtime');
        const socket = new FakeSocket();
        sockets.push(socket);
        return socket;
      },
      setTimer: (callback) => {
        reconnectCallbacks.push(callback);
        return 1 as unknown as ReturnType<typeof setTimeout>;
      },
      clearTimer: () => undefined,
      fetcher: async (input) => {
        const path = String(input);
        if (path === '/api/auth/session') return jsonResponse(authenticatedSession);
        if (path.startsWith('/api/v1/themes/rankings')) return jsonResponse(rankingLive);
        if (path.startsWith('/api/v1/insights/treemap')) return jsonResponse(treemapLive);
        if (path === '/api/v1/auth/realtime-ticket') {
          return jsonResponse(realtimeTicket(`rt_${sockets.length + 1}`));
        }
        throw new Error(`예상하지 못한 요청: ${path}`);
      },
    });

    await repository.getSession();
    await repository.getRankings();
    await repository.getTreemap();
    await vi.waitFor(() => expect(sockets).toHaveLength(1));
    sockets[0].serverOpen();

    const sent = sockets[0].sent.map((message) => JSON.parse(message) as Record<string, unknown>);
    expect(sent[0]).toEqual({ type: 'auth', ticket: 'rt_1' });
    expect(sent[1].type).toBe('subscribe');
    expect(sent[1]).not.toHaveProperty('ticket');

    sockets[0].serverMessage(rankingSnapshot({
      sequence: 1843,
      snapshotId: 'snap_new',
      displayName: '새 전체 스냅샷',
    }));
    expect((await repository.getRankings()).data.items[0].classification.displayName).toBe('새 전체 스냅샷');

    sockets[0].serverMessage(rankingSnapshot({
      sequence: 1843,
      snapshotId: 'snap_duplicate',
      displayName: '중복은 무시',
    }));
    sockets[0].serverMessage(rankingSnapshot({
      sequence: 1841,
      snapshotId: 'snap_old',
      displayName: '역순은 무시',
    }));
    expect((await repository.getRankings()).data.snapshotId).toBe('snap_new');

    sockets[0].serverMessage(treemapSnapshot(1843, 'treemap_same_sequence'));
    expect((await repository.getTreemap()).data.snapshotId).toBe('treemap_same_sequence');

    sockets[0].serverMessage(rankingSnapshot({
      sequence: 1846,
      snapshotId: 'snap_gap_full',
      displayName: 'gap 최신 전체값',
      weightedReturn: 0,
    }));
    const gapResult = await repository.getRankings();
    expect(gapResult.data.snapshotId).toBe('snap_gap_full');
    expect(gapResult.data.items[0].weightedReturn).toBe(0);

    sockets[0].serverClose();
    expect((await repository.getRankings()).meta.marketContext.dataStatus).toBe('DELAYED');
    expect(reconnectCallbacks).toHaveLength(1);
    reconnectCallbacks.shift()?.();
    await vi.waitFor(() => expect(sockets).toHaveLength(2));
    sockets[1].serverOpen();
    sockets[1].serverMessage(rankingSnapshot({
      sequence: 1,
      snapshotId: 'snap_reconnected_full',
      displayName: '재연결 최신 전체값',
    }));
    const reconnected = await repository.getRankings();
    expect(reconnected.data.sequence).toBe(1);
    expect(reconnected.data.snapshotId).toBe('snap_reconnected_full');
    expect(reconnected.meta.marketContext.dataStatus).toBe('LIVE');
  });
});
