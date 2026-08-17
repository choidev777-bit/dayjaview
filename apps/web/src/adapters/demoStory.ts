/**
 * 시연용 데이터. `scripts/build_demo_from_legacy.py`가 구 DAY-JA-VIEW DB에서 뽑아 만든
 * `legacyDemo.json`을 화면 계약 모양으로 바꾼다.
 *
 * 계약 fixture(`contracts/fixtures/**`)는 endpoint별 검증용이라 서로 이어지지 않는다(홈은
 * 원전수출인데 유사사례는 LED가 나온다). 여기서는 같은 하루를 놓고 테마·사유·주도주·과거사례가
 * 한 이야기로 이어지게 한다.
 *
 * 수익률은 구 DB 종가로 계산한 **동일가중**이다. 정본 지표(상한형 유동시총 가중)와 다르므로
 * 상장주식수가 확보되면 이 파일을 버리고 실제 파이프라인 값을 쓴다. 지어낸 값은 없다.
 *
 * fixture adapter에서만 import하며 production 번들에 들어가지 않는다.
 */
import intradayReplay from './intradayReplay.json';
import legacy from './legacyDemo.json';
import type {
  CatalystDetailResponse,
  CatalystTop3Response,
  HistoricalEventResponse,
  HistoricalHorizon,
  HistoricalOutcome,
  RankingResponse,
  ResponseMeta,
  SimilarEventsResponse,
  TreemapResponse,
} from '../domain/contracts';

interface LegacyOutcome {
  horizonTradingDays: number;
  return: number | null;
  status: string;
  unavailableReason: string | null;
}

interface LegacySimilar {
  matchedEventId: string;
  marketDate: string;
  displayNameAtEvent: string;
  normalizedCatalystSummary: string;
  similarityReasons: string[];
  outcomes: LegacyOutcome[];
  leaders: Array<{ stockId: string; symbol: string; name: string; return: number; role: string }>;
}

interface LegacyEvidence {
  newsId: string;
  sourceName: string;
  title: string;
  summary: string;
}

interface LegacyCatalyst {
  catalystId: string;
  catalystName: string;
  matchesToday: boolean;
  horizons: Array<{
    horizonTradingDays: number;
    eligibleCount: number;
    observedCount: number;
    positiveCount: number;
    medianReturn: number | null;
  }>;
  events: Array<{
    matchedEventId: string;
    marketDate: string;
    displayNameAtEvent: string;
    normalizedCatalystSummary: string;
    sameDayReturn: number | null;
    leaderName: string | null;
    similarityReasons: string[];
    outcomes: LegacyOutcome[];
    leaders: Array<{ stockId: string; symbol: string; name: string; return: number; role: string }>;
  }>;
}

interface LegacyTheme {
  rank: number;
  catalysts: LegacyCatalyst[];
  attentionGapTradingDays: number | null;
  evidence: LegacyEvidence[];
  themeId: string;
  eventId: string;
  displayName: string;
  weightedReturn: number | null;
  advancingCount: number;
  validCount: number;
  reason: string;
  leaders: Array<{ stockId: string; symbol: string; name: string; return: number }>;
  similar: LegacySimilar[];
}

const story = legacy as unknown as { marketDate: string; themes: LegacyTheme[] };
const asOf = `${story.marketDate}T06:20:00.000Z`;

const meta: ResponseMeta = {
  requestId: 'req_legacy_demo',
  apiVersion: '1',
  schemaVersion: '2026-08-14.1',
  generatedAt: asOf,
};

const marketContext = {
  market: 'KRX',
  timeZone: 'Asia/Seoul',
  marketDate: story.marketDate,
  asOf,
  dataStatus: 'CLOSED' as const,
  lastHealthyAt: asOf,
  qualityFlags: [] as string[],
};

function outcomes(rows: LegacyOutcome[]): HistoricalOutcome[] {
  return rows.map((row) => ({
    horizonTradingDays: row.horizonTradingDays as HistoricalHorizon,
    return: row.return,
    status: row.status === 'OBSERVED' ? 'OBSERVED' : 'PENDING',
    unavailableReason: row.unavailableReason,
  }));
}

/** 기간별 유효 분모는 관측된 것만 센다. 관찰 미완료를 분모에 넣으면 비율이 부풀려진다. */
function summaryOf(items: LegacySimilar[]) {
  return ([1, 5, 20] as const).map((horizon) => {
    const observed = items
      .map((item) => item.outcomes.find((row) => row.horizonTradingDays === horizon))
      .filter((row): row is LegacyOutcome => Boolean(row && row.return !== null))
      .map((row) => row.return as number);
    const sorted = [...observed].sort((left, right) => left - right);
    const middle = Math.floor(sorted.length / 2);
    return {
      horizonTradingDays: horizon,
      eligibleCount: items.length,
      observedCount: observed.length,
      positiveCount: observed.filter((value) => value > 0).length,
      medianReturn: sorted.length
        ? sorted.length % 2
          ? sorted[middle]
          : (sorted[middle - 1] + sorted[middle]) / 2
        : null,
    };
  });
}

export const demoRankings: RankingResponse = {
  data: {
    snapshotId: 'snap_legacy_demo',
    streamId: `stream_legacy_${story.marketDate}`,
    sequence: 1,
    items: story.themes.map((theme) => ({
      eventId: theme.eventId,
      lifecycleStatus: 'ACTIVE' as const,
      reconciliationStatus: 'MATCHED' as const,
      classification: {
        classificationVersion: 1,
        themeId: theme.themeId,
        displayName: theme.displayName,
        kind: 'INFOSTOCK_THEME' as const,
        certainty: 'CONFIRMED' as const,
        source: 'INFOSTOCK' as const,
        changedAt: asOf,
      },
      rank: theme.rank,
      rankChange60s: null,
      // 급부상은 60초 순위 변동으로 판정한다. 구 DB에는 장중 스냅샷이 없어 계산할 수 없으므로
      // 달지 않는다. 실시간 수집이 붙으면 파이프라인이 채운다.
      badges: [],
      weightedReturn: theme.weightedReturn,
      weightMethod: 'FREE_FLOAT_CAPPED' as const,
      advancingCount: theme.advancingCount,
      validCount: theme.validCount,
      leader: theme.leaders[0]
        ? {
            stockId: theme.leaders[0].stockId,
            symbol: theme.leaders[0].symbol,
            name: theme.leaders[0].name,
            return: theme.leaders[0].return,
          }
        : null,
      evidence: {
        evidenceStatus: 'AFTER_CLOSE_CONFIRMED' as const,
        summary: theme.reason || null,
        publishedAt: asOf,
      },
      coverage: {
        status: 'SUFFICIENT' as const,
        core: {
          observedCount: theme.validCount,
          totalCount: theme.validCount,
          countRatio: 1,
          observedWeightRatio: 1,
        },
        related: {
          observedCount: theme.validCount,
          totalCount: theme.validCount,
          countRatio: 1,
        },
      },
      qualityFlags: [],
    })),
  },
  meta: { ...meta, marketContext },
};

export const demoTreemap = {
  data: {
    snapshotId: 'snap_legacy_treemap',
    streamId: `stream_legacy_${story.marketDate}`,
    sequence: 1,
    items: story.themes
      .filter((theme) => theme.weightedReturn !== null)
      .map((theme) => ({
        eventId: theme.eventId,
        themeId: theme.themeId,
        displayName: theme.displayName,
        lifecycleStatus: 'ACTIVE' as const,
        weightedReturn: theme.weightedReturn as number,
        advancingCount: theme.advancingCount,
        validCount: theme.validCount,
        coverageStatus: 'SUFFICIENT' as const,
        qualityFlags: [] as string[],
      })),
  },
  meta: { ...meta, marketContext },
};

/** 그날 그 테마의 사건 문장을 근거 항목으로 둔다. 뉴스 수집분이 없어 원문 링크는 없다. */
export const demoEvidenceByEvent: Record<string, unknown> = Object.fromEntries(
  story.themes.map((theme) => [
    theme.eventId,
    {
      data: {
        eventId: theme.eventId,
        evidenceStatus: 'AFTER_CLOSE_CONFIRMED',
        items: theme.evidence.map((item) => ({
          newsId: item.newsId,
          sourceName: item.sourceName,
          title: item.title,
          publishedAt: asOf,
          receivedAt: asOf,
          originalUrl: 'https://infostock.co.kr',
          matchBasis: ['THEME'],
          summary: item.summary,
          qualityFlags: [],
        })),
        page: { nextCursor: null, hasMore: false, limit: 20 },
      },
      // marketContext가 없으면 화면이 이 근거가 어느 장의 것인지 못 가려서 장중 이력을 매번 버린다.
      meta: { ...meta, marketContext },
    },
  ]),
);

/**
 * 시연용 장중 근거 이력.
 *
 * 실제로는 사용자가 장중에 화면을 봐야 이력이 쌓인다(세션 보관). 시연은 장 마감 뒤
 * 데이터로 도는 데다 하루가 이미 끝나 있어서 `장중 분석 이력` 탭이 늘 비어 있었다.
 * 그래서 그날 장중에 봤을 법한 상태를 미리 만들어 둔다.
 *
 * 장중이라 아직 확정 전이므로 상태는 `SINGLE_SOURCE`(뉴스 기반 추정)로 두고,
 * 확정 사유보다 이른 시각을 붙인다. 확정본과 문장이 달라야 `장중과 달라졌다`가 보인다.
 */
const LIVE_OBSERVED_AT = `${story.marketDate}T11:40:00+09:00`;

export const demoLiveHistoryByEvent: Record<
  string,
  {
    evidenceStatus: 'SINGLE_SOURCE';
    summary: string | null;
    items: unknown[];
    observedAt: string;
    marketDate: string;
  }
> = Object.fromEntries(
  story.themes
    .filter((theme) => theme.evidence.length > 0)
    .map((theme) => [
      theme.eventId,
      {
        evidenceStatus: 'SINGLE_SOURCE' as const,
        summary: null,
        items: theme.evidence.slice(0, 2).map((item) => ({
          newsId: `${item.newsId}-live`,
          sourceName: item.sourceName,
          title: item.title,
          publishedAt: LIVE_OBSERVED_AT,
          receivedAt: LIVE_OBSERVED_AT,
          originalUrl: 'https://infostock.co.kr',
          matchBasis: ['THEME'],
          summary: item.summary,
          qualityFlags: [],
        })),
        observedAt: LIVE_OBSERVED_AT,
        marketDate: story.marketDate,
      },
    ]),
);

const byTheme = new Map(story.themes.map((theme) => [theme.themeId, theme]));
const byEvent = new Map(story.themes.map((theme) => [theme.eventId, theme]));

export const demoSimilarByEvent: Record<string, SimilarEventsResponse> = Object.fromEntries(
  story.themes.map((theme) => [
    theme.eventId,
    {
      data: {
        eventId: theme.eventId,
        decisionAt: asOf,
        availability: 'AVAILABLE' as const,
        summary: summaryOf(theme.similar),
        items: theme.similar.map((item) => ({
          matchedEventId: item.matchedEventId,
          marketDate: item.marketDate,
          displayNameAtEvent: item.displayNameAtEvent,
          normalizedCatalystSummary: item.normalizedCatalystSummary || '기록된 사유 없음',
          similarityReasons: item.similarityReasons,
          outcomes: outcomes(item.outcomes),
        })),
        page: { nextCursor: null, hasMore: false, limit: 20 },
      },
      meta,
    },
  ]),
);

/** 어느 테마에서 들어왔는지 모르면 1위 테마를 쓴다. */
export const demoSimilarEvents: SimilarEventsResponse =
  demoSimilarByEvent[story.themes[0]?.eventId] ?? {
    data: {
      eventId: 'evt_none',
      decisionAt: asOf,
      availability: 'UNAVAILABLE',
      summary: [],
      items: [],
      page: { nextCursor: null, hasMore: false, limit: 20 },
    },
    meta,
  };

/** 사건 상세. 유사사례와 소재 상세에서 들어오는 사건을 모두 같은 형태로 만든다. */
function historicalEvent(item: {
  matchedEventId: string;
  marketDate: string;
  displayNameAtEvent: string;
  normalizedCatalystSummary: string;
  similarityReasons: string[];
  outcomes: LegacyOutcome[];
  leaders: Array<{ stockId: string; symbol: string; name: string; return: number; role: string }>;
}): [string, HistoricalEventResponse] {
  return [
    item.matchedEventId,
    {
      data: {
        eventId: item.matchedEventId,
        marketDate: item.marketDate,
        displayNameAtEvent: item.displayNameAtEvent,
        catalystSummary: item.normalizedCatalystSummary || '기록된 사유 없음',
        similarityReasons: item.similarityReasons,
        leaders: item.leaders.map((leader) => ({
          stockId: leader.stockId,
          symbol: leader.symbol,
          name: leader.name,
          return: leader.return,
          role: 'LEADER' as const,
        })),
        outcomes: outcomes(item.outcomes),
        futureOutcomeExcludedFromSelection: true as const,
      },
      meta,
    },
  ];
}

export const demoHistoricalEvents: Record<string, HistoricalEventResponse> = Object.fromEntries([
  ...story.themes.flatMap((theme) =>
    theme.similar.map((item) => historicalEvent({ ...item, displayNameAtEvent: item.displayNameAtEvent })),
  ),
  // 소재 상세에서 들어오는 사건도 같은 실데이터로 잇는다. 없으면 계약 fixture로 빠진다.
  ...story.themes.flatMap((theme) =>
    theme.catalysts.flatMap((catalyst) => catalyst.events.map((item) => historicalEvent(item))),
  ),
]);

/**
 * 소재 유형. 온톨로지 라벨(E-17)은 아직 없어서, 그 테마의 과거 사건에 실제로 붙어 있던
 * 키워드를 유형 대신 쓴다. 건수·중앙값은 그 키워드가 붙은 사건만 세어 계산한 값이다.
 */
function horizonRows(catalyst: LegacyCatalyst) {
  return catalyst.horizons.map((row) => ({
    horizonTradingDays: row.horizonTradingDays as HistoricalHorizon,
    eligibleCount: row.eligibleCount,
    observedCount: row.observedCount,
    positiveCount: row.positiveCount,
    medianReturn: row.medianReturn,
  }));
}

export const demoCatalystTop3ByTheme: Record<string, CatalystTop3Response> = Object.fromEntries(
  story.themes.map((theme) => [
    theme.themeId,
    {
      data: {
        themeId: theme.themeId,
        eventId: theme.eventId,
        items: theme.catalysts.map((catalyst) => ({
          catalystId: catalyst.catalystId,
          catalystName: catalyst.catalystName,
          eligibleCount: catalyst.horizons[0]?.eligibleCount ?? 0,
          observedCount: catalyst.horizons[0]?.observedCount ?? 0,
          medianSameDayReturn: catalyst.horizons[0]?.medianReturn ?? null,
          matchesToday: catalyst.matchesToday,
        })),
        qualityNote: '소재 유형은 온톨로지 검증 전이라 사건에 기록된 키워드로 대신합니다.',
      },
      meta,
    },
  ]),
);

export const demoCatalystTop3: CatalystTop3Response =
  demoCatalystTop3ByTheme[story.themes[0]?.themeId] ?? {
    data: { themeId: '', eventId: '', items: [], qualityNote: null },
    meta,
  };

export const demoCatalystDetails: Record<string, CatalystDetailResponse> = Object.fromEntries(
  story.themes.flatMap((theme) =>
    theme.catalysts.map((catalyst) => {
      const rows = horizonRows(catalyst);
      return [
        catalyst.catalystId,
        {
          data: {
            catalystId: catalyst.catalystId,
            themeId: theme.themeId,
            themeDisplayName: theme.displayName,
            catalystName: catalyst.catalystName,
            availability: 'AVAILABLE' as const,
            sameDay: rows[0],
            horizons: rows,
            events: catalyst.events.map((event) => ({
              matchedEventId: event.matchedEventId,
              marketDate: event.marketDate,
              normalizedCatalystSummary: event.normalizedCatalystSummary || '기록된 사유 없음',
              sameDayReturn: event.sameDayReturn,
              leaderName: event.leaderName,
            })),
            qualityNote: '소재 유형은 온톨로지 검증 전이라 사건에 기록된 키워드로 대신합니다.',
          },
          meta,
        },
      ];
    }),
  ),
);

export { byTheme as demoThemeById, byEvent as demoThemeByEvent };


/**
 * 장중 재생 시연.
 *
 * 2026-08-14 장중 09:00~10:39에 실제로 들어온 체결(키움 실시간 `0B`)에서 뽑은
 * 분 단위 스냅샷이다. 데모 테마의 주도 종목 등락률을 동일가중해 테마 수익률로 삼았다.
 * 실제 서비스는 상한형 유동시총 가중이지만(PD-001), 이 화면의 목적은 값이 장중에
 * 실제로 움직이는 걸 보여주는 것이다.
 *
 * 수집은 10:39에 중단됐지만(manifest `status: INTERRUPTED`), 그날 오전에 시장이 내려가
 * 10시 무렵부터는 `weightedReturn > 0`인 테마가 1개 이하로 줄어 트리맵이 비었다.
 * screen_spec 6.3이 상승 테마만 올리도록 정해 놓아 화면이 정직하게 비는 것이라,
 * 상승 테마가 5개 이상 유지되는 09:00~09:56 구간만 남겨 반복한다.
 */
const replay = intradayReplay as { marketDate: string; snapshots: { at: string; items: { themeId: string; weightedReturn: number; advancingCount: number; validCount: number }[] }[] };

export const demoReplayLength = replay.snapshots.length;

/** `step`번째 스냅샷으로 갈아끼운 트리맵 응답. 범위를 넘으면 마지막에서 멈춘다. */
export function demoReplayTreemap(step: number): TreemapResponse {
  const snapshot = replay.snapshots[Math.min(Math.max(step, 0), replay.snapshots.length - 1)];
  const bySnapshot = new Map(snapshot.items.map((row) => [row.themeId, row]));
  const items = demoTreemap.data.items
    .map((tile) => {
      const live = bySnapshot.get(tile.themeId);
      return live
        ? { ...tile, weightedReturn: live.weightedReturn, advancingCount: live.advancingCount, validCount: live.validCount }
        : null;
    })
    .filter((tile): tile is NonNullable<typeof tile> => tile !== null);

  const asOfAt = `${replay.marketDate}T${snapshot.at}:00+09:00`;
  return {
    ...demoTreemap,
    data: { ...demoTreemap.data, items },
    meta: {
      ...demoTreemap.meta,
      marketContext: {
        ...marketContext,
        marketDate: replay.marketDate,
        asOf: asOfAt,
        dataStatus: 'LIVE' as const,
        lastHealthyAt: asOfAt,
      },
    },
  };
}
