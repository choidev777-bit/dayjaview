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

interface LegacyTheme {
  rank: number;
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
      badges: theme.rank === 1 ? ['RISING_FAST'] : [],
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

export const demoHistoricalEvents: Record<string, HistoricalEventResponse> = Object.fromEntries(
  story.themes.flatMap((theme) =>
    theme.similar.map((item) => [
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
    ]),
  ),
);

/** 소재 유형 분류는 온톨로지(E-17) 몫이라 아직 없다. 테마 단위 기록 하나로만 둔다. */
export const demoCatalystTop3ByTheme: Record<string, CatalystTop3Response> = Object.fromEntries(
  story.themes.map((theme) => {
    const rows = summaryOf(theme.similar);
    return [
      theme.themeId,
      {
        data: {
          themeId: theme.themeId,
          eventId: theme.eventId,
          items: [
            {
              catalystId: `ctl_${theme.themeId}`,
              catalystName: `${theme.displayName} 과거 기록`,
              eligibleCount: rows[0].eligibleCount,
              observedCount: rows[0].observedCount,
              medianSameDayReturn: rows[0].medianReturn,
              matchesToday: false,
            },
          ],
          qualityNote: '소재 유형 분류는 온톨로지 검증 뒤에 붙습니다.',
        },
        meta,
      },
    ];
  }),
);

export const demoCatalystTop3: CatalystTop3Response =
  demoCatalystTop3ByTheme[story.themes[0]?.themeId] ?? {
    data: { themeId: '', eventId: '', items: [], qualityNote: null },
    meta,
  };

export const demoCatalystDetails: Record<string, CatalystDetailResponse> = Object.fromEntries(
  story.themes.map((theme) => {
    const rows = summaryOf(theme.similar);
    return [
      `ctl_${theme.themeId}`,
      {
        data: {
          catalystId: `ctl_${theme.themeId}`,
          themeId: theme.themeId,
          themeDisplayName: theme.displayName,
          catalystName: `${theme.displayName} 과거 기록`,
          availability: 'AVAILABLE' as const,
          sameDay: rows[0],
          horizons: rows,
          events: theme.similar.map((item) => ({
            matchedEventId: item.matchedEventId,
            marketDate: item.marketDate,
            normalizedCatalystSummary: item.normalizedCatalystSummary || '기록된 사유 없음',
            sameDayReturn:
              item.outcomes.find((row) => row.horizonTradingDays === 1)?.return ?? null,
            leaderName: item.leaders[0]?.name ?? null,
          })),
          qualityNote: '소재 유형 분류는 온톨로지 검증 뒤에 붙습니다.',
        },
        meta,
      },
    ];
  }),
);

export { byTheme as demoThemeById, byEvent as demoThemeByEvent };
