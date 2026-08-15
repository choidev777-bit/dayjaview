import { Link } from 'react-router-dom';
import { useRepository } from '../app/RepositoryContext';
import { formatReturn, returnTone } from '../domain/formatting';
import { DataStatusBar } from '../shared/DataStatusBar';
import { EmptyState, ErrorState, LoadingState } from '../shared/StatePanel';
import { useRepositoryResource } from '../shared/useRepositoryResource';

export function InsightsPage() {
  const repository = useRepository();
  const resource = useRepositoryResource(
    repository,
    'treemap',
    () => repository.getTreemap(),
    [repository],
  );

  if (resource.status === 'loading') return <LoadingState label="실시간 테마 맵을 준비하는 중입니다" />;
  if (resource.status === 'error') return <ErrorState error={resource.error} retry={resource.retry} />;

  const { data, meta } = resource.data;
  const context = meta.marketContext;

  return (
    <div className="page page--insights">
      <header className="page-intro">
        <h1>실시간 테마 중계</h1>
        {context.dataStatus === 'LIVE' ? (
          <div className="live-status">
            <span className="live-dot" aria-hidden="true" />
            LIVE
          </div>
        ) : null}
      </header>
      <div className="page-status">
        <DataStatusBar context={context} />
      </div>
      <p className="state-note">면적은 테마의 실시간 강도에 따라 달라져요.</p>
      {data.items.length ? (
        <div className="treemap" aria-label="실시간 테마 강도 지도">
          {data.items.map((item) => (
            <Link
              key={item.eventId}
              className="treemap__tile"
              to={`/themes/${encodeURIComponent(item.themeId)}/events/${encodeURIComponent(item.eventId)}`}
              state={{ from: '/insights' }}
              aria-label={`${item.displayName}, 테마 수익률 ${formatReturn(item.weightedReturn)}, 관련주 ${item.advancingCount} / ${item.validCount}종목 상승`}
            >
              <strong>{item.displayName}</strong>
              <b className={returnTone(item.weightedReturn)}>{formatReturn(item.weightedReturn)}</b>
              <small>
                {item.advancingCount} / {item.validCount}종목 상승
              </small>
            </Link>
          ))}
        </div>
      ) : (
        <EmptyState
          title="현재 조건을 충족한 테마가 없습니다"
          description="Coverage 기준에 미달한 테마는 0% 타일로 표시하지 않습니다."
        />
      )}
      <div className="treemap-legend">
        <span>
          <i aria-hidden="true" />
          면적: 테마 강도
        </span>
        <span>수치는 장중 갱신</span>
      </div>
      <p className="section-note notice">
        면적과 색상은 같은 테마 수익률 원값을 사용합니다. 내부 순위 점수는 사용하지 않습니다.
      </p>
    </div>
  );
}
