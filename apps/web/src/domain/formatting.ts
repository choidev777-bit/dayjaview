import type {
  CoverageStatus,
  DataStatus,
  EvidenceStatus,
  HistoricalHorizon,
  HistoricalOutcome,
  LifecycleStatus,
  MatchBasis,
  ReconciliationStatus,
} from './contracts';

const dateFormatter = new Intl.DateTimeFormat('ko-KR', {
  timeZone: 'Asia/Seoul',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
});

const timeFormatter = new Intl.DateTimeFormat('ko-KR', {
  timeZone: 'Asia/Seoul',
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
});

export function formatReturn(value: number | null): string {
  if (value === null) return '—';
  const percent = value * 100;
  const sign = percent > 0 ? '+' : percent < 0 ? '−' : '';
  return `${sign}${Math.abs(percent).toLocaleString('ko-KR', {
    minimumFractionDigits: 1,
    maximumFractionDigits: 1,
  })}%`;
}

export function returnTone(value: number | null): 'market-up' | 'market-down' | 'market-flat' {
  if (value === null || value === 0) return 'market-flat';
  return value > 0 ? 'market-up' : 'market-down';
}

export function formatDate(value: string): string {
  return dateFormatter.format(new Date(value)).replace(/\. /g, '.').replace(/\.$/, '');
}

/** 시안 홈 제목의 날짜 형식. 목록의 날짜(`2024.07.18`)와 달리 문장처럼 읽는 자리다. */
export function formatLongDate(value: string): string {
  const parts = dateFormatter.formatToParts(new Date(value));
  const pick = (type: string) => parts.find((part) => part.type === type)?.value ?? '';
  return `${pick('year')}년 ${pick('month')}월 ${pick('day')}일`;
}

export function formatTime(value: string | null): string {
  return value ? timeFormatter.format(new Date(value)) : '—';
}

export function dataStatusLabel(status: DataStatus): string {
  return {
    PREOPEN: '장 시작 전',
    LIVE: '실시간',
    DELAYED: '수신 지연',
    DEGRADED: '일부 데이터 지연',
    CLOSED: '장 마감',
  }[status];
}

export function evidenceStatusLabel(status: EvidenceStatus): string {
  return {
    SEARCHING: '상승 이유 확인 중',
    SINGLE_SOURCE: '뉴스 기반 추정',
    MULTI_SOURCE_CONFIRMED: '복수 뉴스 확인',
    NO_NEW_CATALYST: '확인된 신규 소재 없음',
    REEMERGENCE: '기존 소재 재부각',
    AFTER_CLOSE_CONFIRMED: '인포스탁 기준 확정',
  }[status];
}

export function evidenceStatusNote(status: EvidenceStatus): string {
  return {
    SEARCHING: '확인된 근거가 생기기 전에는 상승 이유를 만들지 않습니다.',
    SINGLE_SOURCE: '단일 매체 보도로 확인한 추정입니다. 확정된 원인이 아닙니다.',
    MULTI_SOURCE_CONFIRMED: '독립 매체 복수 보도에서 확인된 범위만 요약했습니다.',
    NO_NEW_CATALYST: '현재까지 확인된 기사 범위에서 새 소재를 찾지 못했습니다.',
    REEMERGENCE: '이전에 확인된 소재가 다시 부각됐습니다.',
    AFTER_CLOSE_CONFIRMED: '장 마감 후 인포스탁 기준으로 확정된 사유입니다.',
  }[status];
}

/** 근거가 확인된 상태에서만 상승 이유 문장을 노출한다 (screen_spec 4.2·8.3). */
export function hasConfirmedEvidence(status: EvidenceStatus): boolean {
  return (
    status === 'SINGLE_SOURCE' ||
    status === 'MULTI_SOURCE_CONFIRMED' ||
    status === 'REEMERGENCE' ||
    status === 'AFTER_CLOSE_CONFIRMED'
  );
}

/** 기사별 품질 flag. 내부 코드는 그대로 보여주지 않고 아는 것만 문구로 바꾼다. */
export function evidenceFlagLabel(flag: string): string | null {
  return (
    { PUBLISHED_AT_MISSING: '발행 시각 미확인', RIGHTS_LIMITED: '원문 제공 범위 제한' }[flag] ?? null
  );
}

export function matchBasisLabel(basis: MatchBasis): string {
  return { THEME: '테마 일치', STOCK: '종목 일치', TIME: '시각 근접' }[basis];
}

/** Coverage는 `몇 개 중 몇 개`보다 `믿을 만한가`가 먼저다. 분모·분자는 CoverageIndicator가 따로 보여준다. */
export function coverageStatusLabel(status: CoverageStatus): string {
  return { SUFFICIENT: '충분', PARTIAL: '일부', INSUFFICIENT: '부족' }[status];
}

/** `5 거래일`만 두면 5일째인지 5일 뒤인지 헷갈린다. 뒤로 세는 값이라 `+`를 붙인다. */
export function horizonLabel(horizon: HistoricalHorizon): string {
  return { 1: '다음날', 5: '+5 거래일', 20: '+20 거래일' }[horizon];
}

/** 결측(UNAVAILABLE)과 관찰 미완료(PENDING)를 0%나 같은 문구로 뭉뚱그리지 않는다 (screen_spec 10.5). */
export function outcomeText(outcome: HistoricalOutcome | undefined): {
  text: string;
  tone: ReturnType<typeof returnTone>;
} {
  if (!outcome || outcome.status === 'UNAVAILABLE') {
    return { text: '기록 없음', tone: 'market-flat' };
  }
  if (outcome.status === 'PENDING') return { text: '관찰 중', tone: 'market-flat' };
  return { text: formatReturn(outcome.return), tone: returnTone(outcome.return) };
}

export function eventStatusLabel(
  lifecycle: LifecycleStatus,
  reconciliation: ReconciliationStatus,
): string {
  if (lifecycle === 'CLOSED' && reconciliation === 'PENDING') return '장후 확정 대기';
  if (reconciliation === 'UNMATCHED') return '확정 대기';
  return {
    CANDIDATE: '탐색 중',
    ACTIVE: '활성',
    WEAKENING: '약화',
    CLOSED: '장후 확정',
    DISCARDED: '종료',
  }[lifecycle];
}

export function safeReturnTo(value: string): string {
  if (!value.startsWith('/') || value.startsWith('//') || value.includes('\\')) return '/today';
  try {
    const url = new URL(value, 'https://dayjaview.vercel.app');
    return url.origin === 'https://dayjaview.vercel.app' ? `${url.pathname}${url.search}${url.hash}` : '/today';
  } catch {
    return '/today';
  }
}
