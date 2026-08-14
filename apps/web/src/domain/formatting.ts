import type { DataStatus, EvidenceStatus, LifecycleStatus, ReconciliationStatus } from './contracts';

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
