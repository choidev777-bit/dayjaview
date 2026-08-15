import type {
  DataStatus,
  EvidenceStatus,
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

export function formatDate(value: string): string {
  return dateFormatter.format(new Date(value)).replace(/\. /g, '.').replace(/\.$/, '');
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

export function matchBasisLabel(basis: MatchBasis): string {
  return { THEME: '테마 일치', STOCK: '종목 일치', TIME: '시각 근접' }[basis];
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
