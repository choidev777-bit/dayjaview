/**
 * 시연용 원전수출 이야기.
 *
 * `contracts/fixtures/**`는 endpoint별로 따로 만들어진 계약 검증용이라 서로 이어지지 않는다
 * (홈은 원전수출인데 유사사례는 LED가 나온다). 계약 fixture를 고치면 검증 대상이 흐려지므로
 * 화면을 이어서 보여줄 때 쓰는 데이터만 여기 따로 둔다.
 *
 * 계약 응답과 같은 모양이지만 계약은 아니다. fixture adapter에서만 import하며 production 번들에
 * 들어가지 않는다. 수치는 실제 값이 아니라 화면 검토용 예시다.
 */
import type {
  CatalystDetailResponse,
  CatalystTop3Response,
  HistoricalEventResponse,
  RankingResponse,
  ResponseMeta,
  SimilarEventsResponse,
} from '../domain/contracts';

const meta: ResponseMeta = {
  requestId: 'req_demo_nuclear',
  apiVersion: '1',
  schemaVersion: '2026-08-14.1',
  generatedAt: '2026-08-14T01:18:23.042Z',
};

const THEME_ID = 'thm_nuclear';
const THEME_NAME = '원전수출';

interface DemoEvent {
  id: string;
  marketDate: string;
  summary: string;
  reasons: string[];
  leaders: Array<[string, number]>;
  /** [T+1, T+5, T+20]. number는 관측값, 'PENDING'은 관찰 미완료, 'MISSING'은 가격 결측. */
  outcomes: [DemoOutcome, DemoOutcome, DemoOutcome];
}

type DemoOutcome = number | 'PENDING' | 'MISSING';

const EVENTS: DemoEvent[] = [
  {
    id: 'evt_demo_czech',
    marketDate: '2024-07-18',
    summary: '체코 원전 우선협상대상자 선정',
    reasons: ['해외 수주 단계 진전', '관련 종목군 중첩'],
    leaders: [
      ['두산에너빌리티', 0.142],
      ['한전기술', 0.071],
      ['우리기술', 0.058],
    ],
    outcomes: [0.021, 0.046, 0.072],
  },
  {
    id: 'evt_demo_korus',
    marketDate: '2023-04-25',
    summary: '한미 원전 협력 확대 발표',
    reasons: ['국가 협력 유형', '정책 발표 동반'],
    leaders: [
      ['한전기술', 0.033],
      ['보성파워텍', 0.019],
    ],
    outcomes: [-0.004, -0.012, 0.008],
  },
  {
    id: 'evt_demo_poland',
    marketDate: '2022-10-31',
    summary: '폴란드 원전 개발계획 협력',
    reasons: ['해외 수주 단계 진전', '유사 소재 유형'],
    leaders: [
      ['두산에너빌리티', 0.084],
      ['우진', 0.042],
    ],
    outcomes: [0.009, 0.033, 0.051],
  },
  {
    id: 'evt_demo_eldaba',
    marketDate: '2021-06-09',
    summary: '이집트 엘다바 원전 기자재 수주',
    reasons: ['기자재 공급 유형', '관련 종목군 중첩'],
    leaders: [
      ['두산에너빌리티', 0.061],
      ['한신기계', 0.036],
    ],
    outcomes: [0.052, 0.094, -0.013],
  },
  {
    id: 'evt_demo_saudi',
    marketDate: '2025-11-12',
    summary: '사우디 원전 협력 MOU 체결',
    reasons: ['국가 협력 유형', '해외 수주 단계 진전'],
    leaders: [
      ['두산에너빌리티', 0.118],
      ['한전기술', 0.093],
    ],
    // T+20은 아직 관찰이 끝나지 않았다. 결측이 아니라 관찰 중이다.
    outcomes: [0.068, 0.121, 'PENDING'],
  },
  {
    id: 'evt_demo_supply',
    marketDate: '2026-02-03',
    summary: '원전 기자재 공급망 확대 발표',
    reasons: ['기자재 공급 유형'],
    leaders: [['우리기술', 0.027]],
    // T+5는 당시 가격을 확보하지 못했다. 관찰 중과 구분해서 표시한다.
    outcomes: [0.014, 'MISSING', 'PENDING'],
  },
];

const HORIZONS = [1, 5, 20] as const;

function outcome(value: DemoOutcome, horizon: (typeof HORIZONS)[number]) {
  if (value === 'PENDING') {
    return { horizonTradingDays: horizon, return: null, status: 'PENDING' as const, unavailableReason: null };
  }
  if (value === 'MISSING') {
    return {
      horizonTradingDays: horizon,
      return: null,
      status: 'UNAVAILABLE' as const,
      unavailableReason: 'PRICE_MISSING',
    };
  }
  return { horizonTradingDays: horizon, return: value, status: 'OBSERVED' as const, unavailableReason: null };
}

function outcomesOf(event: DemoEvent) {
  return HORIZONS.map((horizon, index) => outcome(event.outcomes[index], horizon));
}

function median(values: number[]): number | null {
  if (!values.length) return null;
  const sorted = [...values].sort((left, right) => left - right);
  const middle = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
}

/** 서버가 확정해 내려줄 값이다. 여기서는 표시값이 서로 어긋나지 않게 같은 표본에서 만든다. */
function summaryOf() {
  return HORIZONS.map((horizon, index) => {
    const observed = EVENTS.map((event) => event.outcomes[index]).filter(
      (value): value is number => typeof value === 'number',
    );
    return {
      horizonTradingDays: horizon,
      eligibleCount: EVENTS.length,
      observedCount: observed.length,
      positiveCount: observed.filter((value) => value > 0).length,
      medianReturn: median(observed),
    };
  });
}

/**
 * 순위 휠 동작을 볼 수 있게 10개를 채운 오늘 목록. 계약 fixture(`rankings/live.json`)는 항목이
 * 1개라 회전·키보드 이동을 확인할 수 없다. 1위만 급부상 뱃지를 달고, 뒤로 갈수록 수익률·확산이
 * 낮아지도록 둬서 정렬이 눈에 보이게 했다.
 */
const RANKED_THEMES: Array<[name: string, weightedReturn: number, advancing: number, valid: number]> = [
  ['원전수출', 0.027, 17, 21],
  ['반도체 장비', 0.024, 14, 18],
  ['전력설비', 0.021, 11, 15],
  ['방산', 0.018, 9, 13],
  ['로봇', 0.014, 8, 12],
  ['전선', 0.012, 7, 11],
  ['LED 장비', 0.011, 9, 16],
  ['건설 중소형', 0.009, 12, 22],
  ['반도체 기판', 0.008, 6, 10],
  ['AI 인프라', 0.007, 8, 17],
];

const rankingsMeta = {
  ...meta,
  requestId: 'req_demo_rankings',
  marketContext: {
    market: 'KRX',
    timeZone: 'Asia/Seoul',
    marketDate: '2026-08-14',
    asOf: '2026-08-14T01:18:22.410Z',
    dataStatus: 'LIVE' as const,
    lastHealthyAt: '2026-08-14T01:18:22.410Z',
    qualityFlags: [],
  },
};

export const demoRankings: RankingResponse = {
  data: {
    snapshotId: 'snap_demo_rankings',
    streamId: 'stream_demo_20260814',
    sequence: 1842,
    items: RANKED_THEMES.map(([name, weightedReturn, advancing, valid], index) => ({
      eventId: index === 0 ? 'evt_current' : `evt_demo_rank_${index + 1}`,
      lifecycleStatus: 'ACTIVE' as const,
      reconciliationStatus: 'PENDING' as const,
      classification: {
        classificationVersion: 1,
        themeId: index === 0 ? THEME_ID : `thm_demo_${index + 1}`,
        displayName: name,
        kind: 'INFOSTOCK_THEME' as const,
        certainty: 'PROVISIONAL' as const,
        source: 'LIVE_ENGINE' as const,
        changedAt: '2026-08-14T00:11:04.000Z',
      },
      rank: index + 1,
      rankChange60s: index === 0 ? 3 : 0,
      badges: index === 0 ? ['RISING_FAST'] : [],
      weightedReturn,
      weightMethod: 'FREE_FLOAT_CAPPED' as const,
      advancingCount: advancing,
      validCount: valid,
      leader: null,
      evidence: {
        evidenceStatus: 'SEARCHING' as const,
        summary: null,
        publishedAt: null,
      },
      coverage: {
        status: 'SUFFICIENT' as const,
        core: {
          observedCount: advancing,
          totalCount: valid,
          countRatio: advancing / valid,
          observedWeightRatio: 0.91,
        },
        related: { observedCount: valid, totalCount: valid + 6, countRatio: valid / (valid + 6) },
      },
      qualityFlags: [],
    })),
  },
  meta: rankingsMeta as RankingResponse['meta'],
};

export const demoSimilarEvents: SimilarEventsResponse = {
  data: {
    eventId: 'evt_current',
    decisionAt: '2026-08-14T01:18:22.410Z',
    availability: 'AVAILABLE',
    summary: summaryOf(),
    items: EVENTS.map((event) => ({
      matchedEventId: event.id,
      marketDate: event.marketDate,
      displayNameAtEvent: THEME_NAME,
      normalizedCatalystSummary: event.summary,
      similarityReasons: event.reasons,
      outcomes: outcomesOf(event),
    })),
    page: { nextCursor: null, hasMore: false, limit: 20 },
  },
  meta,
};

export const demoHistoricalEvents: Record<string, HistoricalEventResponse> = Object.fromEntries(
  EVENTS.map((event) => [
    event.id,
    {
      data: {
        eventId: event.id,
        marketDate: event.marketDate,
        displayNameAtEvent: THEME_NAME,
        catalystSummary: event.summary,
        similarityReasons: event.reasons,
        leaders: event.leaders.map(([name, value], index) => ({
          stockId: `stk_demo_${event.id}_${index}`,
          symbol: `${100000 + index}`,
          name,
          return: value,
          role: 'LEADER' as const,
        })),
        outcomes: outcomesOf(event),
        futureOutcomeExcludedFromSelection: true as const,
      },
      meta,
    },
  ]),
);

interface DemoCatalyst {
  id: string;
  name: string;
  eventIds: string[];
  /** 당일·T+1·T+5·T+20 중앙 반응과 유효 표본. 서버 확정값 자리다. */
  rows: Array<[eligible: number, observed: number, positive: number, medianReturn: number]>;
  matchesToday: boolean;
}

const CATALYSTS: DemoCatalyst[] = [
  {
    id: 'ctl_overseas_order',
    name: '해외 원전 수주 단계 진전',
    eventIds: ['evt_demo_czech', 'evt_demo_poland', 'evt_demo_saudi'],
    rows: [
      [12, 12, 8, 0.064],
      [12, 12, 8, 0.021],
      [12, 11, 7, 0.038],
      [12, 8, 4, 0.012],
    ],
    matchesToday: true,
  },
  {
    id: 'ctl_export_policy',
    name: '원전 수출 정책 발표',
    eventIds: ['evt_demo_korus'],
    rows: [
      [9, 9, 5, 0.031],
      [9, 9, 5, 0.014],
      [9, 8, 4, 0.019],
      [9, 6, 3, 0.007],
    ],
    matchesToday: false,
  },
  {
    id: 'ctl_equipment_supply',
    name: '원전 기자재 공급 계약',
    eventIds: ['evt_demo_eldaba', 'evt_demo_supply'],
    rows: [
      [7, 7, 4, 0.022],
      [7, 7, 4, 0.011],
      [7, 6, 3, 0.016],
      [7, 4, 2, 0.004],
    ],
    matchesToday: false,
  },
];

function summaryRow(catalyst: DemoCatalyst, index: number, horizon: 1 | 5 | 20) {
  const [eligibleCount, observedCount, positiveCount, medianReturn] = catalyst.rows[index];
  return { horizonTradingDays: horizon, eligibleCount, observedCount, positiveCount, medianReturn };
}

export const demoCatalystTop3: CatalystTop3Response = {
  data: {
    themeId: THEME_ID,
    eventId: 'evt_current',
    items: CATALYSTS.map((catalyst) => ({
      catalystId: catalyst.id,
      catalystName: catalyst.name,
      eligibleCount: catalyst.rows[0][0],
      observedCount: catalyst.rows[0][1],
      medianSameDayReturn: catalyst.rows[0][3],
      matchesToday: catalyst.matchesToday,
    })),
    qualityNote: '룰 기반 키워드라 검수 전 노이즈가 있을 수 있어요.',
  },
  meta,
};

export const demoCatalystDetails: Record<string, CatalystDetailResponse> = Object.fromEntries(
  CATALYSTS.map((catalyst) => [
    catalyst.id,
    {
      data: {
        catalystId: catalyst.id,
        themeId: THEME_ID,
        themeDisplayName: THEME_NAME,
        catalystName: catalyst.name,
        availability: 'AVAILABLE' as const,
        sameDay: summaryRow(catalyst, 0, 1),
        horizons: [summaryRow(catalyst, 1, 1), summaryRow(catalyst, 2, 5), summaryRow(catalyst, 3, 20)],
        events: catalyst.eventIds.map((eventId) => {
          const event = EVENTS.find((candidate) => candidate.id === eventId);
          return {
            matchedEventId: eventId,
            marketDate: event?.marketDate ?? '',
            normalizedCatalystSummary: event?.summary ?? '',
            sameDayReturn: typeof event?.outcomes[0] === 'number' ? event.outcomes[0] : null,
            leaderName: event?.leaders[0]?.[0] ?? null,
          };
        }),
        qualityNote: '룰 기반 키워드라 검수 전 노이즈가 있을 수 있어요.',
      },
      meta,
    },
  ]),
);
