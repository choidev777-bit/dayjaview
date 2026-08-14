import type { ReactNode } from 'react';
import { useRepository } from '../app/RepositoryContext';
import type { EvidenceItem, EvidenceResponse, EvidenceStatus, MarketContext } from '../domain/contracts';
import {
  evidenceStatusLabel,
  formatDateTime,
  safeOriginalSourceUrl,
} from '../domain/formatting';
import { EmptyState, ErrorState, LoadingState } from './StatePanel';
import { useRepositoryResource } from './useRepositoryResource';

function evidenceStatusDescription(status: EvidenceStatus): string {
  switch (status) {
    case 'SEARCHING':
      return '확인된 근거가 생기기 전에는 상승 이유를 만들지 않습니다.';
    case 'SINGLE_SOURCE':
      return '단일 뉴스 근거이며 확인된 보도 범위를 넘어 인과를 확정하지 않습니다.';
    case 'MULTI_SOURCE_CONFIRMED':
      return '복수 출처에서 확인된 보도 범위만 표시합니다.';
    case 'NO_NEW_CATALYST':
      return '현재까지 확인된 기사 범위에서 새 소재를 찾지 못했습니다.';
    case 'REEMERGENCE':
      return '기존 소재와의 연결 근거가 확인된 상태입니다.';
    case 'AFTER_CLOSE_CONFIRMED':
      return '장후 인포스탁 기준으로 확정된 상태입니다.';
    default:
      return '근거 상태를 확인하고 있습니다.';
  }
}

function sourceStatus(item: EvidenceItem): string {
  if (item.qualityFlags.includes('PUBLISHED_TIME_UNKNOWN') || item.publishedAt === null) {
    return '발행 시각 미확인';
  }
  if (
    item.qualityFlags.includes('SOURCE_DEGRADED') ||
    item.qualityFlags.includes('STALE_NEWS_DATA')
  ) {
    return '출처 데이터 지연';
  }
  if (item.qualityFlags.length) return '품질 확인 필요';
  return '정상 수신';
}

function matchBasisLabel(value: string): string {
  return {
    THEME: '테마',
    STOCK: '종목',
    TIME: '시각',
  }[value] ?? '기타';
}

function EvidenceFreshnessNotice({ context }: { context?: MarketContext }) {
  if (!context || (context.dataStatus !== 'DELAYED' && context.dataStatus !== 'DEGRADED')) {
    return null;
  }

  const stale = context.qualityFlags.includes('STALE_NEWS_DATA');
  return (
    <aside className="evidence-freshness" role="status" aria-label="근거 데이터 제공 상태">
      <strong>
        {context.dataStatus === 'DELAYED' ? '근거 데이터 수신 지연' : '일부 출처 수집 지연'}
      </strong>
      <p>
        기준 {formatDateTime(context.asOf)}
        {context.lastHealthyAt ? ` · 마지막 정상 ${formatDateTime(context.lastHealthyAt)}` : ''}
      </p>
      {stale ? <p>근거 데이터가 오래되었습니다. 최신 원인으로 단정하지 않습니다.</p> : null}
    </aside>
  );
}

function EvidenceSource({ item, evidenceStatus }: { item: EvidenceItem; evidenceStatus: EvidenceStatus }) {
  const originalUrl = safeOriginalSourceUrl(item.originalUrl);
  return (
    <li className="evidence-source">
      <div className="evidence-source__heading">
        <div>
          <p className="eyebrow">출처</p>
          <h3>{item.title}</h3>
        </div>
        <span className="status-chip">{sourceStatus(item)}</span>
      </div>
      <p className="evidence-source__summary">{item.summary}</p>
      <p className="evidence-source__summary-note">DAYJAVIEW 요약 · 원문이 아닙니다</p>
      <dl className="evidence-source__metadata">
        <div>
          <dt>매체</dt>
          <dd>{item.sourceName}</dd>
        </div>
        <div>
          <dt>발행</dt>
          <dd>{item.publishedAt ? formatDateTime(item.publishedAt) : '발행 시각 미확인'}</dd>
        </div>
        <div>
          <dt>수신</dt>
          <dd>{formatDateTime(item.receivedAt)}</dd>
        </div>
        <div>
          <dt>근거 상태</dt>
          <dd>{evidenceStatusLabel(evidenceStatus)}</dd>
        </div>
        <div>
          <dt>연결 기준</dt>
          <dd>{item.matchBasis.map(matchBasisLabel).join(' · ')}</dd>
        </div>
      </dl>
      {originalUrl ? (
        <a
          className="evidence-source__link"
          href={originalUrl}
          target="_blank"
          rel="noopener noreferrer"
          aria-label={`${item.sourceName} 원문 보기: ${item.title}`}
        >
          원문 보기 <span aria-hidden="true">↗</span>
        </a>
      ) : (
        <span className="evidence-source__link evidence-source__link--unavailable">
          원문 링크 제공 안 됨
        </span>
      )}
    </li>
  );
}

function EvidenceSectionFrame({ children }: { children: ReactNode }) {
  return (
    <section className="detail-section evidence-section" aria-labelledby="evidence-title">
      {children}
    </section>
  );
}

export function EvidenceSection({ eventId }: { eventId: string }) {
  const repository = useRepository();
  const resource = useRepositoryResource<EvidenceResponse>(
    repository,
    'evidence',
    () => repository.getEvidence(eventId),
    [repository, eventId],
  );

  if (resource.status === 'loading') {
    return (
      <EvidenceSectionFrame>
        <h2 id="evidence-title">확인된 기사 근거</h2>
        <LoadingState label="기사 근거를 확인하는 중입니다" />
      </EvidenceSectionFrame>
    );
  }
  if (resource.status === 'error') {
    return (
      <EvidenceSectionFrame>
        <h2 id="evidence-title">확인된 기사 근거</h2>
        <ErrorState error={resource.error} retry={resource.retry} />
      </EvidenceSectionFrame>
    );
  }

  const { data, meta } = resource.data;
  const degraded = meta.marketContext?.dataStatus === 'DEGRADED';
  const emptyDescription = degraded
    ? '수집 상태가 정상화되기 전에는 상승 이유를 확정하거나 만들지 않습니다.'
    : data.evidenceStatus === 'SEARCHING' || data.evidenceStatus === 'NO_NEW_CATALYST'
      ? evidenceStatusDescription(data.evidenceStatus)
      : '표시 가능한 원문 출처가 없습니다. 출처 데이터가 없으면 상승 이유를 새로 만들지 않습니다.';

  return (
    <EvidenceSectionFrame>
      <div className="section-heading">
        <div>
          <p className="eyebrow">근거 상태</p>
          <h2 id="evidence-title">확인된 기사 근거</h2>
        </div>
        <span className="status-chip">{evidenceStatusLabel(data.evidenceStatus)}</span>
      </div>
      {data.items.length ? (
        <p className="evidence-section__status-description">
          {evidenceStatusDescription(data.evidenceStatus)}
        </p>
      ) : null}
      <p className="section-note">
        Event {data.eventId} · 목록 생성 {formatDateTime(meta.generatedAt)}
      </p>
      <EvidenceFreshnessNotice context={meta.marketContext} />
      {data.items.length ? (
        <ul className="evidence-list">
          {data.items.map((item) => (
            <EvidenceSource key={item.newsId} item={item} evidenceStatus={data.evidenceStatus} />
          ))}
        </ul>
      ) : (
        <EmptyState title={evidenceStatusLabel(data.evidenceStatus)} description={emptyDescription} />
      )}
    </EvidenceSectionFrame>
  );
}
