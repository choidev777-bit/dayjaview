import type { KeyboardEvent } from 'react';
import { Link } from 'react-router-dom';
import type { RankingItem } from '../domain/contracts';
import { evidenceStatusLabel, eventStatusLabel, formatReturn, formatTime } from '../domain/formatting';
import { useRepository } from '../app/RepositoryContext';
import { CoverageIndicator } from '../shared/CoverageIndicator';
import { DataStatusBar } from '../shared/DataStatusBar';
import { EmptyState, ErrorState, LoadingState } from '../shared/StatePanel';
import { useRepositoryResource } from '../shared/useRepositoryResource';

function cardBadge(item: RankingItem): string | null {
  if (item.badges.includes('RISING_FAST')) return '급부상';
  if (item.classification.kind === 'TEMPORARY_THEME') return '신규·임시';
  if (item.lifecycleStatus === 'WEAKENING') return '약화';
  return null;
}

function activateLinkWithSpace(event: KeyboardEvent<HTMLAnchorElement>) {
  if (event.key === ' ') {
    event.preventDefault();
    event.currentTarget.click();
  }
}

function ThemeRankCard({ item }: { item: RankingItem }) {
  const badge = cardBadge(item);
  const metricUnavailable = item.coverage.status === 'INSUFFICIENT' || item.weightedReturn === null;
  const path = `/themes/${encodeURIComponent(item.classification.themeId)}/events/${encodeURIComponent(item.eventId)}`;

  return (
    <article className="theme-card">
      <Link
        className="theme-card__link"
        to={path}
        state={{ from: '/today' }}
        onKeyDown={activateLinkWithSpace}
        aria-label={`${item.rank}위 ${item.classification.displayName}, 테마 수익률 ${formatReturn(item.weightedReturn)}`}
      >
        <div className="theme-card__heading">
          <span className="rank" aria-label={`${item.rank}위`}>
            {item.rank}
          </span>
          <div>
            <h2>{item.classification.displayName}</h2>
            <span className="event-state">
              {eventStatusLabel(item.lifecycleStatus, item.reconciliationStatus)}
            </span>
          </div>
          {badge ? <span className="badge">{badge}</span> : null}
        </div>

        <div className="theme-card__metric">
          <span>테마 수익률</span>
          <strong className={metricUnavailable ? '' : 'market-up'}>{formatReturn(item.weightedReturn)}</strong>
        </div>

        <div className="theme-card__evidence">
          <strong>{item.evidence.summary ?? evidenceStatusLabel(item.evidence.evidenceStatus)}</strong>
          <span>
            {evidenceStatusLabel(item.evidence.evidenceStatus)}
            {item.evidence.publishedAt ? ` · ${formatTime(item.evidence.publishedAt)}` : ''}
          </span>
        </div>

        {item.leader ? (
          <p className="leader-line">
            <span>대표 주도주 {item.leader.name}</span>
            <strong className="market-up">{formatReturn(item.leader.return)}</strong>
          </p>
        ) : null}

        {item.advancingCount !== null && item.validCount !== null ? (
          <p className="ratio-line">
            관련주 {item.advancingCount.toLocaleString('ko-KR')} / {item.validCount.toLocaleString('ko-KR')}종목 상승
          </p>
        ) : null}

        <CoverageIndicator coverage={item.coverage} />
      </Link>
    </article>
  );
}

export function TodayPage() {
  const repository = useRepository();
  const resource = useRepositoryResource(
    repository,
    'rankings',
    () => repository.getRankings(),
    [repository],
  );

  if (resource.status === 'loading') return <LoadingState label="오늘의 테마를 불러오는 중입니다" />;
  if (resource.status === 'error') return <ErrorState error={resource.error} retry={resource.retry} />;

  const { data, meta } = resource.data;
  const context = meta.marketContext;

  return (
    <div className="page page--today">
      <header className="page-header">
        <p className="eyebrow">{context.marketDate}</p>
        <h1>오늘</h1>
        <p>현재 시장에서 강한 테마를 확인하세요.</p>
      </header>
      <DataStatusBar context={context} />
      <section aria-labelledby="strong-themes-title">
        <div className="section-heading">
          <div>
            <p className="eyebrow">시장 반응</p>
            <h2 id="strong-themes-title">지금 강한 테마</h2>
          </div>
          <span className="timestamp">기준 {formatTime(context.asOf)}</span>
        </div>
        {data.items.length ? (
          <div className="theme-list">{data.items.map((item) => <ThemeRankCard key={item.eventId} item={item} />)}</div>
        ) : (
          <EmptyState
            title={context.dataStatus === 'DELAYED' ? '마지막 정상 화면을 불러오는 중입니다' : '현재 조건을 충족한 테마가 없습니다'}
            description={context.dataStatus === 'DELAYED' ? '수신이 정상화되면 마지막 정상값부터 이어서 보여드립니다.' : '조건을 충족한 활성 테마가 생기면 이곳에 표시됩니다.'}
          />
        )}
      </section>
    </div>
  );
}
